const fs = require('fs');
const path = require('path');

const targetClient = path.join(__dirname, 'node_modules', 'whatsapp-web.js', 'src', 'Client.js');
const targetPuppeteer = path.join(__dirname, 'node_modules', 'whatsapp-web.js', 'src', 'util', 'Puppeteer.js');

const patchClient = path.join(__dirname, 'patches', 'Client.js');
const patchPuppeteer = path.join(__dirname, 'patches', 'Puppeteer.js');

if (fs.existsSync(targetClient) && fs.existsSync(patchClient)) {
    fs.copyFileSync(patchClient, targetClient);
    console.log('[Patch] Client.js patched successfully');
}

if (fs.existsSync(targetPuppeteer) && fs.existsSync(patchPuppeteer)) {
    fs.copyFileSync(patchPuppeteer, targetPuppeteer);
    console.log('[Patch] Puppeteer.js patched successfully');
}
