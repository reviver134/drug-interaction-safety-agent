import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    headless: true,
    trace: 'retain-on-failure',
    ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1280, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    // UI tests exercise the live graph with deterministic explanation fallback.
    // verify_live_prescription.py separately exercises the real Gemini provider.
    env: { GEMINI_API_KEY: '', GOOGLE_API_KEY: '' },
    command: process.platform === 'win32'
      ? '..\\venv\\Scripts\\python.exe -m uvicorn api:app --app-dir .. --host 127.0.0.1 --port 8765'
      : '../venv/bin/python -m uvicorn api:app --app-dir .. --host 127.0.0.1 --port 8765',
    url: 'http://127.0.0.1:8765/api/config',
    reuseExistingServer: false,
  },
});
