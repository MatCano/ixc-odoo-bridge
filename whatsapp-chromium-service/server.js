const express = require('express');
const cors = require('cors');
const path = require('path');
const { initDatabase, getDb } = require('./db');
const SessionManager = require('./session-manager');

function createServer(options = {}) {
  const app = express();
  app.use(cors());
  app.use(express.json({ limit: '50mb' }));
  app.use(express.urlencoded({ extended: true, limit: '50mb' }));

  const db = options.db || (options.dbPath ? initDatabase(options.dbPath) : getDb());
  const sessionManager = options.sessionManager || new SessionManager({
    db,
    dataDir: options.dataDir || path.resolve(__dirname, '../data/whatsapp'),
    odooWebhookUrl: options.odooWebhookUrl || process.env.ODOO_WEBHOOK_URL,
    chromeBin: options.chromeBin || process.env.CHROME_BIN
  });

  const mediaDir = path.join(options.dataDir || path.resolve(__dirname, '../data/whatsapp'), 'media');
  app.use('/media', express.static(mediaDir));

  // Health
  app.get('/health', (req, res) => {
    res.json({ status: 'ok', timestamp: new Date().toISOString() });
  });

  // Session status
  app.get('/api/session/:userId/status', (req, res) => {
    const userId = req.params.userId;
    const status = sessionManager.getSessionStatus(userId);
    res.json(status);
  });

  // Start session (launch Chromium / generate QR)
  app.post('/api/session/:userId/start', async (req, res) => {
    const userId = req.params.userId;
    try {
      const result = await sessionManager.startSession(userId);
      res.json(result);
    } catch (err) {
      console.error(`Error starting session for user ${userId}:`, err);
      res.status(500).json({ error: err.message });
    }
  });

  // Disconnect session
  app.post('/api/session/:userId/disconnect', async (req, res) => {
    const userId = req.params.userId;
    try {
      const result = await sessionManager.disconnectSession(userId);
      res.json(result);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // Get contacts
  app.get('/api/session/:userId/contacts', async (req, res) => {
    const uid = sessionManager.normalizeUserId(req.params.userId);
    try {
      const contacts = db.getContacts(uid);
      res.json(contacts);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // Sync contacts from WhatsApp
  app.post('/api/session/:userId/sync-contacts', async (req, res) => {
    const userId = req.params.userId;
    try {
      const contacts = await sessionManager.syncContacts(userId);
      res.json(contacts);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // Get message history for a specific chat
  app.get('/api/session/:userId/messages/:chatId', (req, res) => {
    const uid = sessionManager.normalizeUserId(req.params.userId);
    const chatId = sessionManager.formatChatId(req.params.chatId);
    const limit = parseInt(req.query.limit, 10) || 100;
    try {
      const messages = db.getChatMessages(uid, chatId, limit);
      res.json(messages);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // Send message (text, voice, image, video)
  app.post('/api/session/:userId/send', async (req, res) => {
    const userId = req.params.userId;
    const { to, message, mediaBase64, mediaMime, mediaFilename, isVoice } = req.body;

    try {
      const result = await sessionManager.sendMessage(userId, {
        to,
        message,
        mediaBase64,
        mediaMime,
        mediaFilename,
        isVoice
      });
      res.json(result);
    } catch (err) {
      console.error(`Error sending message for user ${userId}:`, err);
      res.status(500).json({ error: err.message });
    }
  });

  // Mark chat as read
  app.post('/api/session/:userId/read', (req, res) => {
    const uid = sessionManager.normalizeUserId(req.params.userId);
    const { chatId } = req.body;
    try {
      const formattedChatId = sessionManager.formatChatId(chatId);
      db.markChatAsRead(uid, formattedChatId);
      res.json({ success: true, chatId: formattedChatId });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // Mock incoming message for testing / webhook simulation
  app.post('/api/test/mock-incoming', async (req, res) => {
    const { userId, from, body, mediaType, mediaBase64, mediaMime, filename } = req.body;
    const uid = sessionManager.normalizeUserId(userId);
    try {
      const chatId = sessionManager.formatChatId(from);
      const messageId = `mock_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`;
      let mediaUrl = null;

      if (mediaBase64 && mediaMime) {
        const userMediaDir = path.join(mediaDir, uid);
        const fs = require('fs');
        fs.mkdirSync(userMediaDir, { recursive: true });
        const ext = filename ? path.extname(filename) : (mediaMime.split('/')[1]?.split(';')[0] || 'bin');
        const safeName = `${messageId}.${ext}`;
        fs.writeFileSync(path.join(userMediaDir, safeName), Buffer.from(mediaBase64, 'base64'));
        mediaUrl = `/media/${uid}/${safeName}`;
      }

      const nowTs = Math.floor(Date.now() / 1000);
      db.saveMessage({
        id: messageId,
        user_id: uid,
        chat_id: chatId,
        sender: chatId,
        from_me: 0,
        type: mediaType || 'text',
        body: body || '',
        media_url: mediaUrl,
        media_mime: mediaMime,
        media_filename: filename,
        timestamp: nowTs,
        status: 'received'
      });

      db.upsertContact(uid, {
        jid: chatId,
        number: chatId.replace('@c.us', ''),
        name: chatId.replace('@c.us', ''),
        push_name: 'Test Contact',
        unread_count: 1
      });

      // Dispatch webhook
      await sessionManager.dispatchWebhook({
        user_id: uid,
        message_id: messageId,
        chat_id: chatId,
        from_me: 0,
        sender: chatId,
        phone: chatId.replace('@c.us', ''),
        body: body || '',
        type: mediaType || 'text',
        media_url: mediaUrl,
        timestamp: nowTs
      });

      res.json({ success: true, messageId, chatId });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  return { app, db, sessionManager };
}

if (require.main === module) {
  process.on('uncaughtException', (err) => {
    console.error('[Process] Uncaught exception:', err.message);
  });
  process.on('unhandledRejection', (reason) => {
    console.error('[Process] Unhandled rejection:', reason);
  });

  const PORT = process.env.PORT || 3001;
  const { app, sessionManager } = createServer();
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[WhatsApp Chromium Service] Listening on http://0.0.0.0:${PORT}`);
    // Auto-restaura a sessão autenticada do usuário 5
    setTimeout(() => {
      console.log('[Auto-Start] Inicializando sessão persistida para user_5...');
      sessionManager.startSession('user_5').catch(err => {
        console.warn('[Auto-Start] Aviso ao auto-iniciar user_5:', err.message);
      });
    }, 1500);
  });
}

module.exports = { createServer };
