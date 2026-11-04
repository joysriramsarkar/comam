const { spawn } = require('child_process');
const path = require('path');

const root = path.resolve(__dirname, '..');
const backendDir = path.join(root, 'backend');
const frontendDir = path.join(root, 'frontend');

// Use the virtualenv's python/uvicorn
const isWin = process.platform === 'win32';
const uvicornBin = isWin
  ? path.join(backendDir, '.venv', 'Scripts', 'uvicorn.exe')
  : path.join(backendDir, '.venv', 'bin', 'uvicorn');

console.log('\x1b[36m%s\x1b[0m', '================================================');
console.log('\x1b[36m%s\x1b[0m', '      Starting Comam AI (Backend + Frontend)    ');
console.log('\x1b[36m%s\x1b[0m', '================================================\n');

// 1. Start Python Backend on port 8000
console.log('\x1b[32m%s\x1b[0m', '[1/2] Starting Python Backend on http://localhost:8000 ...');
const backend = spawn(
  uvicornBin,
  ['app.main:app', '--port', '8000', '--reload'],
  {
    cwd: backendDir,
    stdio: 'inherit',
    shell: true,
  }
);

backend.on('error', (err) => {
  console.error('\x1b[31mBackend failed to start:\x1b[0m', err.message);
});

// 2. Start Vite Frontend on port 5173
console.log('\x1b[34m%s\x1b[0m', '[2/2] Starting React Vite Frontend on http://localhost:5173 ...\n');
const npmCmd = isWin ? 'npm.cmd' : 'npm';
const frontend = spawn(npmCmd, ['run', 'dev'], {
  cwd: frontendDir,
  stdio: 'inherit',
  shell: true,
});

frontend.on('error', (err) => {
  console.error('\x1b[31mFrontend failed to start:\x1b[0m', err.message);
});

// Clean shutdown on Ctrl+C
const cleanup = () => {
  console.log('\n\x1b[33mShutting down Comam processes...\x1b[0m');
  try { backend.kill(); } catch (e) {}
  try { frontend.kill(); } catch (e) {}
  process.exit(0);
};

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
