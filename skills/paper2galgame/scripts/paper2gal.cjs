#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const MIN_NODE_MAJOR = 18;

function isSupportedNodeVersion(version) {
  const major = Number.parseInt(String(version).replace(/^v/, '').split('.')[0], 10);
  return Number.isInteger(major) && major >= MIN_NODE_MAJOR;
}

function executableExtensions() {
  if (process.platform !== 'win32') return [''];
  return (process.env.PATHEXT || '.EXE;.COM;.CMD;.BAT')
    .split(';')
    .filter(Boolean)
    .map((extension) => extension.toLowerCase());
}

function findCommand(name) {
  const directories = (process.env.PATH || '')
    .split(path.delimiter)
    .map((directory) => directory.replace(/^"|"$/g, ''))
    .filter(Boolean);
  const extensions = executableExtensions();

  for (const directory of directories) {
    for (const extension of extensions) {
      const candidate = path.join(directory, `${name}${extension}`);
      try {
        fs.accessSync(candidate, process.platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
        return candidate;
      } catch {}
    }
  }

  return null;
}

function resolveWindowsShim(command, shimPath) {
  if (process.platform !== 'win32' || !/\.(cmd|bat)$/i.test(shimPath)) {
    return { type: 'command', path: shimPath, args: [] };
  }

  const shimDirectory = path.dirname(shimPath);
  const candidates = command === 'paper2gal'
    ? [
        path.join(shimDirectory, 'node_modules', '@paper2gal', 'cli', 'paper2gal'),
        path.join(shimDirectory, 'node_modules', '@paper2gal', 'cli', 'paper2gal.mjs'),
      ]
    : [
        path.join(shimDirectory, 'node_modules', 'npm', 'bin', 'npx-cli.js'),
        path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npx-cli.js'),
      ];
  const script = candidates.find((candidate) => fs.existsSync(candidate));

  return script ? { type: 'node', path: script, args: [] } : null;
}

function resolveCli() {
  if (process.env.P2G_CLI_PATH) {
    const explicitPath = path.resolve(process.env.P2G_CLI_PATH);
    if (!fs.existsSync(explicitPath)) {
      throw new Error(`P2G_CLI_PATH does not exist: ${explicitPath}`);
    }
    if (process.platform === 'win32' && /\.(cmd|bat)$/i.test(explicitPath)) {
      throw new Error('P2G_CLI_PATH must point to the CLI JavaScript file, not a .cmd or .bat shim.');
    }
    return { type: /\.[cm]?js$|[\\/]paper2gal$/i.test(explicitPath) ? 'node' : 'command', path: explicitPath, args: [] };
  }

  const localCandidates = [
    path.join(process.cwd(), 'cli', 'paper2gal-cli', 'paper2gal.mjs'),
    path.join(process.cwd(), 'Paper2Gal', 'cli', 'paper2gal-cli', 'paper2gal.mjs'),
  ];
  const localCli = localCandidates.find((candidate) => fs.existsSync(candidate));
  if (localCli) return { type: 'node', path: localCli, args: [] };

  const globalCli = findCommand('paper2gal');
  if (globalCli) {
    const resolvedGlobalCli = resolveWindowsShim('paper2gal', globalCli);
    if (resolvedGlobalCli) return resolvedGlobalCli;
  }

  const npx = findCommand('npx');
  if (npx) {
    const resolvedNpx = resolveWindowsShim('npx', npx);
    if (resolvedNpx) return { ...resolvedNpx, args: ['--yes', '@paper2gal/cli@latest'] };
  }

  throw new Error('Paper2Gal CLI was not found. Install @paper2gal/cli or set P2G_CLI_PATH.');
}

function runCommand(executable, args) {
  return spawnSync(executable, args, { stdio: 'inherit' });
}

function main() {
  if (!isSupportedNodeVersion(process.versions.node)) {
    console.error(`Paper2Gal CLI requires Node.js ${MIN_NODE_MAJOR} or newer; found ${process.version}.`);
    return 1;
  }

  try {
    const cli = resolveCli();
    const forwardedArgs = [...cli.args, ...process.argv.slice(2)];
    const result = cli.type === 'node'
      ? spawnSync(process.execPath, [cli.path, ...forwardedArgs], { stdio: 'inherit' })
      : runCommand(cli.path, forwardedArgs);

    if (result.error) throw result.error;
    return Number.isInteger(result.status) ? result.status : 1;
  } catch (error) {
    console.error(`paper2gal launcher: ${error.message}`);
    return 1;
  }
}

module.exports = { findCommand, isSupportedNodeVersion, resolveCli, resolveWindowsShim };

if (require.main === module) process.exitCode = main();
