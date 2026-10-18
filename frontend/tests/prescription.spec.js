import { test, expect } from '@playwright/test';

const photo = name => ({
  name, mimeType: 'image/png',
  buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64'),
});

test('OpenFDA descriptions and source links appear after prescription confirmation', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('Medicine 1', {exact:true}).fill('warfarin');
  await page.getByRole('button', {name:'Match names and aliases'}).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', {name:'Check all medication pairs'}).click();
  const context = page.locator('section.coverage').filter({has: page.getByRole('heading', {name:'OpenFDA medicine descriptions and label context'})});
  await expect(context.getByRole('link', {name:'Read the source label'})).toHaveAttribute('href', /dailymed.*setid=/);
  await expect(context.locator('details[open]').filter({has: page.locator('summary', {hasText:/^description$/})})).toContainText(/warfarin/i);
  await expect(context.locator('summary', {hasText:/^contraindications$/})).toBeVisible();
  await expect(context.getByRole('heading', {name:'About this medicine'})).toBeVisible();
  await context.locator('summary', {hasText:/^Label warnings to review$/}).click();
  const citation = context.getByRole('link', {name:/Label source L1-boxed_warning/});
  await expect(citation).toHaveAttribute('href', '#label-source-L1-boxed_warning');
});

test('photo preview, extraction review, replacement and removal clear stale results', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/config', route => route.fulfill({ json: { gemini_configured: true } }));
  // The provider is controlled here; all subsequent graph queries use the real backend.
  await page.route('**/api/prescription/extract', route => route.fulfill({ json: {
    medications: [
      {name:'clarithromycin',strength:'',formulation:'tablet',uncertain:true,note:'Verify this reading.'},
      {name:'simvastatin',strength:'20 mg',formulation:'tablet',uncertain:false,note:''},
    ], warnings: [], source: 'gemini',
  } }));
  await page.goto('/');
  await page.getByLabel('Upload prescription photos').setInputFiles(photo('prescription.png'));
  await expect(page.getByAltText('Prescription photo 1')).toBeVisible();
  await expect.poll(() => page.getByAltText('Prescription photo 1').evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  await expect(page.getByRole('button', {name: 'Read medications with Gemini'})).toBeEnabled();
  await page.getByRole('button', {name: 'Read medications with Gemini'}).click();
  await expect(page.getByLabel('Medicine 1 · photo 1', {exact: true})).toHaveValue('clarithromycin');
  await expect(page.getByText('Uncertain reading — verify this name')).toBeVisible();
  await page.getByRole('button', {name:'Match names and aliases'}).click();
  await expect(page.getByRole('button', {name:'Check all medication pairs'})).toBeDisabled();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', {name:'Check all medication pairs'}).click();
  await expect(page.getByRole('heading', {name:'3. What the evidence says'})).toBeVisible();
  await expect(page.getByRole('link', {name:'Source E1', exact:true})).toBeVisible();
  await page.getByLabel('Replace photo 1').setInputFiles(photo('replacement.png'));
  await expect(page.getByText('replacement.png', {exact:true})).toBeVisible();
  await expect(page.getByRole('heading', {name:'3. What the evidence says'})).toHaveCount(0);
  await expect(page.getByLabel('Medicine 1', {exact:true})).toHaveValue('');
  await page.getByRole('button', {name:'Remove photo 1'}).click();
  await expect(page.getByAltText('Prescription photo 1')).toHaveCount(0);
  await expect(page.getByRole('button', {name:'Read medications with Gemini'})).toBeDisabled();
  expect(errors).toEqual([]);
});

test('manual names, missing data, alias matching and result invalidation', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('Medicine 1', {exact:true}).fill('clarithromycin');
  await page.getByRole('button', {name:'Add medicine'}).click();
  await page.getByLabel('Medicine 2', {exact:true}).fill('simvastatin');
  await page.getByRole('button', {name:'Add medicine'}).click();
  await page.getByLabel('Medicine 3', {exact:true}).fill('not_a_real_drug_name_999');
  await page.getByRole('button', {name:'Match names and aliases'}).click();
  await expect(page.getByText('Not matched', {exact:true})).toBeVisible();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', {name:'Check all medication pairs'}).click();
  await expect(page.getByText(/interactions involving this medication were not checked/)).toBeVisible();
  await expect(page.getByText('Review needed · major finding')).toBeVisible();
  await page.getByLabel('Medicine 3', {exact:true}).fill('paracetamol');
  await expect(page.getByRole('heading', {name:'3. What the evidence says'})).toHaveCount(0);
  await page.getByRole('button', {name:'Match names and aliases'}).click();
  await expect(page.locator('.match').filter({hasText:'paracetamol'})).toContainText('acetaminophen');
  await expect(page.getByRole('checkbox')).not.toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('removing an image during extraction cannot restore its stale medicines', async ({page}) => {
  await page.route('**/api/config', route => route.fulfill({json:{gemini_configured:true}}));
  let release;
  const wait = new Promise(resolve => {release=resolve;});
  await page.route('**/api/prescription/extract', async route => {
    await wait;
    await route.fulfill({json:{medications:[{name:'stale medicine',strength:'',formulation:'',uncertain:false,note:''}],warnings:[]}}).catch(() => {});
  });
  await page.goto('/');
  await page.getByLabel('Upload prescription photos').setInputFiles(photo('wrong.png'));
  const started = page.waitForRequest('**/api/prescription/extract');
  await page.getByRole('button', {name:'Read medications with Gemini'}).click();
  await started;
  await page.getByRole('button', {name:'Remove photo 1'}).click();
  release();
  await expect(page.getByLabel('Medicine 1', {exact:true})).toHaveValue('');
  await expect(page.getByRole('status')).toHaveCount(0);
});
