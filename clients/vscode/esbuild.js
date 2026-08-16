// Bundles the extension into a single CommonJS file at out/extension.js.
// `vscode` is provided by the extension host at runtime, never bundled.
const esbuild = require('esbuild');

const watch = process.argv.includes('--watch');

const options = {
  entryPoints: ['src/extension.ts'],
  bundle: true,
  outfile: 'out/extension.js',
  external: ['vscode'],
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  sourcemap: true,
  minify: false,
};

async function main() {
  const ctx = await esbuild.context(options);
  if (watch) {
    await ctx.watch();
    console.log('watching for changes...');
  } else {
    await ctx.rebuild();
    await ctx.dispose();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
