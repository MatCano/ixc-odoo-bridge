const { test, describe, before, after } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { initDatabase } = require('../db');
const { createServer } = require('../server');

describe('WhatsApp Chromium REST API & Integration', () => {
  const testDir = path.join(__dirname, 'tmp_server_test');
  const dbPath = path.join(testDir, 'test.db');
  let server;
  let baseUrl;
  let db;
  let sessionManager;

  before(async () => {
    fs.mkdirSync(testDir, { recursive: true });
    db = initDatabase(dbPath);
    const created = createServer({
      db,
      dataDir: testDir,
      odooWebhookUrl: 'http://localhost:9999/test-webhook'
    });
    sessionManager = created.sessionManager;
    const { app } = created;

    server = http.createServer(app);
    await new Promise((resolve) => {
      server.listen(0, '127.0.0.1', () => {
        const port = server.address().port;
        baseUrl = `http://127.0.0.1:${port}`;
        resolve();
      });
    });
  });

  after(async () => {
    await new Promise((resolve) => server.close(resolve));
    db.close();
    fs.rmSync(testDir, { recursive: true, force: true });
  });

  test('GET /health returns status ok', async () => {
    const res = await fetch(`${baseUrl}/health`);
    assert.strictEqual(res.status, 200);
    const body = await res.json();
    assert.strictEqual(body.status, 'ok');
  });

  test('GET /api/session/:userId/status returns initial state', async () => {
    const res = await fetch(`${baseUrl}/api/session/5/status`);
    assert.strictEqual(res.status, 200);
    const body = await res.json();
    assert.strictEqual(body.userId, 'user_5');
    assert.strictEqual(body.status, 'disconnected');
  });

  test('POST /api/session/:userId/send sends text message and saves to SQLite', async () => {
    // Mock active session
    let mockIdCounter = 0;
    sessionManager.sessions.set('user_5', {
      status: 'ready',
      client: {
        sendMessage: async () => ({ id: { id: 'mock_msg_' + (++mockIdCounter) } })
      }
    });

    const res = await fetch(`${baseUrl}/api/session/5/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        to: '5511999998888',
        message: 'Olá, teste de envio direto!'
      })
    });

    assert.strictEqual(res.status, 200);
    const body = await res.json();
    assert.strictEqual(body.success, true);
    assert.strictEqual(body.chatId, '5511999998888@c.us');
    assert.ok(body.messageId);

    // Verify in DB
    const messages = db.getChatMessages('user_5', '5511999998888@c.us');
    assert.strictEqual(messages.length, 1);
    assert.strictEqual(messages[0].body, 'Olá, teste de envio direto!');
    assert.strictEqual(messages[0].from_me, 1);
  });

  test('POST /api/session/:userId/send with voice/audio base64', async () => {
    const fakeBase64 = Buffer.from('fake-audio-data').toString('base64');
    const res = await fetch(`${baseUrl}/api/session/5/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        to: '5511999998888',
        mediaBase64: fakeBase64,
        mediaMime: 'audio/ogg; codecs=opus',
        mediaFilename: 'voice.ogg',
        isVoice: true
      })
    });

    assert.strictEqual(res.status, 200);
    const body = await res.json();
    assert.strictEqual(body.success, true);
    assert.strictEqual(body.mediaType, 'voice');
    assert.ok(body.mediaUrl.includes('/media/user_5/'));

    const messages = db.getChatMessages('user_5', '5511999998888@c.us');
    assert.strictEqual(messages.length, 2);
    assert.strictEqual(messages[1].type, 'voice');
  });

  test('POST /api/test/mock-incoming simulates incoming message with unread tag', async () => {
    const fakeImgBase64 = Buffer.from('fake-image-data').toString('base64');
    const res = await fetch(`${baseUrl}/api/test/mock-incoming`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        userId: 5,
        from: '5511777776666',
        body: 'Foto do comprovante',
        mediaType: 'image',
        mediaBase64: fakeImgBase64,
        mediaMime: 'image/jpeg',
        filename: 'comprovante.jpg'
      })
    });

    assert.strictEqual(res.status, 200);
    const body = await res.json();
    assert.strictEqual(body.success, true);

    // Verify contacts has unread_count = 1
    const contacts = db.getContacts('user_5');
    const c = contacts.find(item => item.jid === '5511777776666@c.us');
    assert.ok(c, 'Contact should exist in contacts list');
    assert.strictEqual(c.unread_count, 1);
  });

  test('POST /api/session/:userId/read resets unread counter', async () => {
    const res = await fetch(`${baseUrl}/api/session/5/read`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chatId: '5511777776666' })
    });

    assert.strictEqual(res.status, 200);
    const contacts = db.getContacts('user_5');
    const c = contacts.find(item => item.jid === '5511777776666@c.us');
    assert.strictEqual(c.unread_count, 0);
  });
});
