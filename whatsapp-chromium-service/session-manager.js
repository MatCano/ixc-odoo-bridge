const path = require('path');
const fs = require('fs');
const qrcode = require('qrcode');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');

class SessionManager {
  constructor({ db, dataDir, odooWebhookUrl, chromeBin } = {}) {
    this.db = db;
    this.dataDir = dataDir || path.resolve(__dirname, '../data/whatsapp');
    this.sessionsDir = path.join(this.dataDir, 'sessions');
    this.mediaDir = path.join(this.dataDir, 'media');
    this.odooWebhookUrl = odooWebhookUrl || process.env.ODOO_WEBHOOK_URL || 'http://localhost:8069/maisfibra/whatsapp/inbound';
    this.chromeBin = chromeBin || process.env.CHROME_BIN || '/usr/bin/google-chrome-stable';

    fs.mkdirSync(this.sessionsDir, { recursive: true });
    fs.mkdirSync(this.mediaDir, { recursive: true });

    this.sessions = new Map();
  }

  normalizeUserId(userId) {
    if (typeof userId === 'string' && userId.startsWith('user_')) {
      return userId;
    }
    return `user_${userId}`;
  }

  formatChatId(phoneOrJid) {
    if (!phoneOrJid) return '';
    let cleaned = phoneOrJid.toString().trim();
    if (cleaned.includes('@')) {
      return cleaned;
    }
    cleaned = cleaned.replace(/\D/g, '');
    if (!cleaned) return '';
    return `${cleaned}@c.us`;
  }

  getSessionStatus(userId) {
    const uid = this.normalizeUserId(userId);
    const memSession = this.sessions.get(uid);
    if (memSession) {
      return {
        userId: uid,
        status: memSession.status,
        qrCode: memSession.qrCode || null,
        phone: memSession.phone || null,
        pushname: memSession.pushname || null,
        lastActive: memSession.lastActive || new Date().toISOString()
      };
    }

    if (this.db) {
      const dbSession = this.db.getSession(uid);
      if (dbSession) {
        return {
          userId: uid,
          status: dbSession.status,
          qrCode: dbSession.qr_code,
          phone: dbSession.phone,
          pushname: dbSession.push_name,
          lastActive: dbSession.updated_at
        };
      }
    }

    return {
      userId: uid,
      status: 'disconnected',
      qrCode: null,
      phone: null,
      pushname: null,
      lastActive: null
    };
  }

  async startSession(userId) {
    const uid = this.normalizeUserId(userId);
    const existing = this.sessions.get(uid);
    if (existing && (existing.status === 'ready' || existing.status === 'initializing' || existing.status === 'qr_ready')) {
      return this.getSessionStatus(uid);
    }

    const sessionState = {
      client: null,
      status: 'initializing',
      qrCode: null,
      phone: null,
      pushname: null,
      lastActive: new Date().toISOString()
    };
    this.sessions.set(uid, sessionState);

    if (this.db) {
      this.db.saveSession(uid, 'initializing', null);
    }

    // Limpa locks residuais do Chromium
    const userSessionPath = path.join(this.sessionsDir, `session-${uid}`);
    if (fs.existsSync(userSessionPath)) {
      try {
        const files = fs.readdirSync(userSessionPath);
        for (const file of files) {
          if (file.startsWith('Singleton')) {
            fs.unlinkSync(path.join(userSessionPath, file));
          }
        }
      } catch (lockErr) {}
    }

    const client = new Client({
      authStrategy: new LocalAuth({
        clientId: uid,
        dataPath: this.sessionsDir
      }),
      authTimeoutMs: 120000,
      userAgent: false,
      puppeteer: {
        timeout: 120000,
        executablePath: this.chromeBin,
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--disable-gpu',
          '--disable-background-timer-throttling',
          '--disable-backgrounding-occluded-windows',
          '--disable-renderer-backgrounding',
          '--disable-features=CalculateNativeWinOcclusion'
        ]
      }
    });

    sessionState.client = client;

    client.on('qr', async (qr) => {
      try {
        const qrDataUrl = await qrcode.toDataURL(qr);
        sessionState.status = 'qr_ready';
        sessionState.qrCode = qrDataUrl;
        sessionState.lastActive = new Date().toISOString();
        if (this.db) {
          this.db.saveSession(uid, 'qr_ready', qrDataUrl);
        }
      } catch (err) {
        console.error(`[WhatsApp ${uid}] QR Error:`, err);
      }
    });

    client.on('authenticated', () => {
      sessionState.status = 'authenticated';
      sessionState.lastActive = new Date().toISOString();
      if (this.db) {
        this.db.saveSession(uid, 'authenticated', null);
      }
    });

    client.on('ready', async () => {
      sessionState.status = 'ready';
      sessionState.qrCode = null;
      sessionState.lastActive = new Date().toISOString();

      try {
        const info = client.info;
        if (info) {
          sessionState.phone = info.wid?.user || null;
          sessionState.pushname = info.pushname || null;
        }

        if (this.db) {
          this.db.saveSession(uid, 'ready', null, sessionState.phone, sessionState.pushname);
        }

        // Notifica Odoo imediatamente sobre a sincronização da sessão
        this.dispatchWebhook({
          event: 'session_ready',
          user_id: uid,
          status: 'ready',
          phone: sessionState.phone,
          push_name: sessionState.pushname
        }).catch(() => {});

        this.syncContacts(uid).catch(err => {
          console.warn(`[WhatsApp ${uid}] Initial contact sync notice:`, err.message);
        });
      } catch (err) {
        console.error(`[WhatsApp ${uid}] Ready handler error:`, err);
      }
    });

    client.on('auth_failure', (msg) => {
      console.error(`[WhatsApp ${uid}] Auth failure:`, msg);
      sessionState.status = 'auth_failure';
      if (this.db) {
        this.db.saveSession(uid, 'auth_failure', null);
      }
    });

    client.on('disconnected', (reason) => {
      console.log(`[WhatsApp ${uid}] Disconnected:`, reason);
      sessionState.status = 'disconnected';
      sessionState.qrCode = null;
      if (this.db) {
        this.db.saveSession(uid, 'disconnected', null);
      }
      this.sessions.delete(uid);

      // Auto-reconnect if not explicitly logged out
      if (reason !== 'LOGOUT') {
        console.log(`[WhatsApp ${uid}] Auto-reconnecting in 5s...`);
        setTimeout(() => {
          this.startSession(uid).catch(reconnectErr => {
            console.warn(`[WhatsApp ${uid}] Auto-reconnect notice:`, reconnectErr.message);
          });
        }, 5000);
      }
    });

    client.on('message', async (msg) => {
      await this.handleIncomingMessage(uid, msg, false);
    });

    client.on('message_create', async (msg) => {
      if (msg.fromMe) {
        await this.handleIncomingMessage(uid, msg, true);
      }
    });

    client.initialize().catch(err => {
      console.error(`[WhatsApp ${uid}] Initialize failed:`, err);
      sessionState.status = 'disconnected';
      if (this.db) {
        this.db.saveSession(uid, 'disconnected', null);
      }
      this.sessions.delete(uid);
    });

    return this.getSessionStatus(uid);
  }

  async disconnectSession(userId) {
    const uid = this.normalizeUserId(userId);
    const session = this.sessions.get(uid);
    if (session && session.client) {
      try {
        await session.client.logout().catch(() => {});
        await session.client.destroy().catch(() => {});
      } catch (err) {}
    }
    this.sessions.delete(uid);
    if (this.db) {
      this.db.saveSession(uid, 'disconnected', null);
    }
    return { success: true, status: 'disconnected' };
  }

  async handleIncomingMessage(userId, msg, fromMe) {
    const uid = this.normalizeUserId(userId);
    const session = this.sessions.get(uid);
    try {
      const chatId = msg.fromMe ? msg.to : msg.from;
      if (!chatId || chatId === 'status@broadcast' || chatId.includes('@g.us')) return;

      const senderJid = msg.author || msg.from;

      // Resolve contact from SQLite
      let contact = this.db ? this.db.findContact(uid, chatId) : null;
      if (!contact && senderJid) {
        contact = this.db ? this.db.findContact(uid, senderJid) : null;
      }

      // If not in SQLite, resolve from browser Store.Contact
      if (!contact && session && session.client && session.client.pupPage) {
        try {
          const browserContact = await session.client.pupPage.evaluate((cid, sid) => {
            if (!window.Store || !window.Store.Contact) return null;
            const target = sid || cid;
            const clean = target.replace(/\D/g, '');
            const m = window.Store.Contact.models.find(c => 
              c.id?._serialized === target ||
              c.lid?._serialized === target ||
              (c.lid?.user && c.lid.user === clean) ||
              (c.id?.user && c.id.user === clean)
            );
            if (m) {
              let phone = '';
              if (m.phoneNumber) phone = m.phoneNumber.user || '';
              if (!phone && m.id?.server === 'c.us') phone = m.id.user;
              return {
                jid: m.id?._serialized,
                lid: m.lid?._serialized,
                number: phone,
                name: m.name || m.pushname || m.formattedName || '',
                pushName: m.pushname || ''
              };
            }
            return null;
          }, chatId, senderJid);

          if (browserContact) {
            contact = browserContact;
            if (this.db) {
              this.db.upsertContact(uid, {
                jid: browserContact.jid || chatId,
                number: browserContact.number,
                lid: browserContact.lid,
                name: browserContact.name,
                push_name: browserContact.pushName
              });
            }
          }
        } catch (err) {}
      }

      let phone = contact?.number || '';
      if (!phone && chatId.includes('@c.us')) {
        phone = chatId.replace('@c.us', '');
      }
      let senderName = contact?.name || msg._data?.notifyName || '';
      if (!senderName || senderName.includes('@') || senderName.startsWith('user_')) {
        senderName = contact?.pushName || phone;
      }

      let mediaType = 'text';
      let mediaUrl = null;
      let mediaMime = null;
      let mediaFilename = null;
      let mediaBase64 = null;

      if (msg.hasMedia) {
        try {
          const media = await msg.downloadMedia();
          if (media) {
            mediaMime = media.mimetype;
            mediaBase64 = media.data;
            if (media.mimetype.startsWith('image/')) mediaType = 'image';
            else if (media.mimetype.startsWith('audio/')) mediaType = 'voice';
            else if (media.mimetype.startsWith('video/')) mediaType = 'video';
            else mediaType = 'document';

            const userMediaDir = path.join(this.mediaDir, uid);
            fs.mkdirSync(userMediaDir, { recursive: true });

            const ext = media.filename ? path.extname(media.filename) : (media.mimetype.split('/')[1]?.split(';')[0] || 'bin');
            const safeName = `${msg.id.id}.${ext}`;
            const diskPath = path.join(userMediaDir, safeName);
            fs.writeFileSync(diskPath, Buffer.from(media.data, 'base64'));

            mediaUrl = `/media/${uid}/${safeName}`;
            mediaFilename = media.filename || safeName;
          }
        } catch (mediaErr) {}
      }

      // Persist to SQLite
      if (this.db) {
        this.db.saveMessage({
          id: msg.id.id,
          user_id: uid,
          chat_id: chatId,
          sender: fromMe ? uid : (contact?.jid || chatId),
          from_me: fromMe ? 1 : 0,
          type: mediaType,
          body: msg.body || '',
          media_url: mediaUrl,
          media_mime: mediaMime,
          media_filename: mediaFilename,
          timestamp: Math.floor((msg.timestamp ? msg.timestamp * 1000 : Date.now()) / 1000),
          status: 'received'
        });

        if (phone) {
          this.db.upsertContact(uid, {
            jid: contact?.jid || chatId,
            number: phone,
            lid: contact?.lid || (chatId.includes('@lid') ? chatId : null),
            name: senderName,
            push_name: msg._data?.notifyName || '',
            unread_count: fromMe ? 0 : 1
          });
        }
      }

      // Webhook dispatch to Odoo
      this.dispatchWebhook({
        user_id: uid,
        message_id: msg.id.id,
        chat_id: chatId,
        from_me: fromMe ? 1 : 0,
        sender: senderName,
        phone: phone,
        body: msg.body || '',
        type: mediaType,
        media_url: mediaUrl,
        media_base64: mediaBase64,
        media_mime: mediaMime,
        media_filename: mediaFilename,
        timestamp: Math.floor((msg.timestamp ? msg.timestamp * 1000 : Date.now()) / 1000)
      }).catch(() => {});

    } catch (err) {
      console.error(`[WhatsApp ${uid}] handleIncomingMessage error:`, err);
    }
  }

  async dispatchWebhook(payload) {
    if (!this.odooWebhookUrl) return false;
    try {
      const response = await fetch(this.odooWebhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      return response.ok;
    } catch (err) {
      return false;
    }
  }

  async syncContacts(userId) {
    const uid = this.normalizeUserId(userId);
    const session = this.sessions.get(uid);
    if (!session || !session.client || session.status !== 'ready') {
      if (this.db) {
        return this.db.getContacts(uid);
      }
      return [];
    }

    try {
      // Evaluate directly in browser to get complete contacts with phone numbers and names
      const browserContacts = await session.client.pupPage.evaluate(() => {
        const list = [];
        if (!window.Store || !window.Store.Contact) return list;
        for (const c of window.Store.Contact.models) {
          const sid = c.id?._serialized || '';
          if (c.isGroup || c.isNewsletter || sid.includes('@g.us') || sid.includes('newsletter') || sid === 'status@broadcast') {
            continue;
          }
          let phone = '';
          if (c.phoneNumber) {
            phone = c.phoneNumber._serialized || c.phoneNumber.user || '';
          }
          if (!phone && c.id && c.id.server === 'c.us') {
            phone = c.id.user;
          }
          const lid = c.lid ? (c.lid._serialized || c.lid.user || '') : '';
          let name = c.name || c.pushname || c.formattedName || '';
          if (name.includes('@')) {
            name = phone ? phone : '';
          }
          if (!phone && !lid) continue;

          list.push({
            jid: sid,
            lid: lid,
            number: phone || '',
            name: name,
            pushName: c.pushname || ''
          });
        }
        return list;
      });

      const saved = [];
      for (const c of browserContacts) {
        if (!c.number && !c.lid) continue;
        const item = {
          jid: c.jid,
          number: c.number,
          lid: c.lid,
          name: c.name || c.number,
          push_name: c.pushName || '',
          unread_count: 0
        };
        if (this.db) {
          this.db.upsertContact(uid, item);
        }
        saved.push(item);
      }

      return saved.length > 0 ? saved : this.db.getContacts(uid);
    } catch (err) {
      console.warn(`[WhatsApp ${uid}] syncContacts error:`, err.message);
      if (this.db) {
        return this.db.getContacts(uid);
      }
      return [];
    }
  }

  async sendMessage(userId, { to, message, mediaBase64, mediaMime, mediaFilename, isVoice }) {
    const uid = this.normalizeUserId(userId);
    let chatId = this.formatChatId(to);
    if (!chatId) {
      throw new Error('Destinatário inválido (número ou chatId não fornecido)');
    }

    const session = this.sessions.get(uid);
    let sentMsg = null;
    let messageId = `msg_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`;
    let mediaType = 'text';
    let mediaUrl = null;

    if (mediaBase64 && mediaMime) {
      if (mediaMime.startsWith('image/')) mediaType = 'image';
      else if (mediaMime.startsWith('audio/')) mediaType = 'voice';
      else if (mediaMime.startsWith('video/')) mediaType = 'video';
      else mediaType = 'document';

      const userMediaDir = path.join(this.mediaDir, uid);
      fs.mkdirSync(userMediaDir, { recursive: true });

      const ext = mediaFilename ? path.extname(mediaFilename) : (mediaMime.split('/')[1]?.split(';')[0] || 'bin');
      const safeName = `${messageId}.${ext}`;
      const diskPath = path.join(userMediaDir, safeName);
      fs.writeFileSync(diskPath, Buffer.from(mediaBase64, 'base64'));

      mediaUrl = `/media/${uid}/${safeName}`;
      mediaFilename = mediaFilename || safeName;
    }

    if (session && session.client && session.status === 'ready') {
      try {
        const cleanTo = to.toString().replace(/\D/g, '');
        if (cleanTo.length >= 10 && cleanTo.length <= 13) {
          chatId = `${cleanTo}@c.us`;
        } else if (this.db) {
          const contact = this.db.findContact(uid, cleanTo);
          if (contact && contact.jid && !contact.jid.includes('@lid')) {
            chatId = contact.jid;
          }
        }

        if (mediaBase64 && mediaMime) {
          const media = new MessageMedia(mediaMime, mediaBase64, mediaFilename);
          const options = {};
          if (isVoice) {
            options.sendAudioAsVoice = true;
          }
          if (message) {
            options.caption = message;
          }
          sentMsg = await session.client.sendMessage(chatId, media, options);
        } else {
          sentMsg = await session.client.sendMessage(chatId, message);
        }

        if (sentMsg && sentMsg.id && sentMsg.id.id) {
          messageId = sentMsg.id.id;
        }
      } catch (sendErr) {
        console.error(`[WhatsApp ${uid}] sendMessage error:`, sendErr);
        throw sendErr;
      }
    } else {
      const currentStatus = session?.status || 'disconnected';
      console.warn(`[WhatsApp ${uid}] Session not READY (current: ${currentStatus}).`);
      throw new Error(`Sessão do WhatsApp não está pronta para envio (status atual: ${currentStatus}). Conecte em Definições > WhatsApp.`);
    }

    // Persist to SQLite
    if (this.db) {
      const nowTs = Math.floor(Date.now() / 1000);
      this.db.saveMessage({
        id: messageId,
        user_id: uid,
        chat_id: chatId,
        sender: uid,
        from_me: 1,
        type: mediaType,
        body: message || (mediaType !== 'text' ? `[${mediaType}]` : ''),
        media_url: mediaUrl,
        media_mime: mediaMime,
        media_filename: mediaFilename,
        timestamp: nowTs,
        status: 'sent'
      });

      const phone = chatId.replace('@c.us', '');
      this.db.upsertContact(uid, {
        jid: chatId,
        number: phone,
        name: phone,
        push_name: '',
        unread_count: 0
      });
    }

    return {
      success: true,
      messageId,
      chatId,
      mediaType,
      mediaUrl,
      timestamp: Math.floor(Date.now() / 1000)
    };
  }
}

module.exports = SessionManager;
