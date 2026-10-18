import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const baseURL = process.env.PREVIEW_URL || 'http://127.0.0.1:8000';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({viewport: {width: 1440, height: 1100}, deviceScaleFactor: 1});
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await mkdir('../verification/design', {recursive: true});
async function capture(name) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({path: `../verification/design/${name}.png`, fullPage: true});
  const geometry = {};
  for (const selector of ['.welcome-hero', '.capture-grid', '.medicine-card', '.graph-panel']) {
    const item = page.locator(selector);
    if(await item.count()) geometry[selector] = await item.boundingBox();
  }
  await writeFile(`../verification/design/${name}.json`, JSON.stringify(geometry, null, 2));
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`Horizontal overflow: ${name}`);
}
try {
  await page.goto(baseURL);
  await capture('welcome-desktop');
  await page.getByRole('button', {name:'Next', exact:true}).click();
  await capture('upload-desktop');
  await page.getByRole('button', {name:'Enter medicines manually'}).click();
  for (const [i,name] of ['warfarin','ibuprofen','lisinopril'].entries()) {
    if(i) await page.getByRole('button', {name:'Add medicine', exact:true}).click();
    await page.getByLabel(`Medicine ${i+1}`, {exact:true}).fill(name);
  }
  await page.getByRole('button', {name:'Match names and aliases'}).click();
  await page.getByRole('checkbox').waitFor({timeout:60000});
  await page.getByRole('checkbox').check();
  await capture('review-desktop');
  await page.getByRole('button', {name:'Check all medication pairs'}).click();
  await page.getByRole('heading', {name:'Possible interactions, in context.'}).waitFor({timeout:90000});
  await capture('results-desktop');
  await page.getByRole('button', {name:'Highlight warfarin'}).click();
  await page.getByRole('tab', {name:'OpenFDA data',exact:true}).click();
  await page.locator('.drug-label > summary').first().click();
  await capture('openfda-desktop');
  await page.getByRole('tab', {name:'For hackathon judges'}).click();
  await capture('judges-desktop');
  await page.getByRole('tab', {name:'Interactions & severity'}).click();
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(() => scrollTo(0,0));
  await capture('results-mobile');
  await page.getByRole('link', {name:/Review medicines/}).click();
  await capture('review-mobile');
  await page.getByRole('link', {name:/Add prescription/}).click();
  await capture('upload-mobile');
  await page.getByRole('link', {name:'Drug Safety home'}).click();
  await capture('welcome-mobile');
  console.log(JSON.stringify({screens:10, consoleErrors:errors}));
  if(errors.length) process.exitCode = 1;
} finally {await browser.close();}
