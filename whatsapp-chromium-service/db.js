const { DatabaseSync } = require('node:sqlite');
const path = require('node:path');
const fs = require('node:fs');

let instance = null;

function initDatabase(customPath) {
    const dbPath = customPath || process.env.SQLITE_DB_PATH || path.join(__dirname, '..', 'data', 'whatsapp', 'whatsapp.db');
    const dbDir = path.dirname(dbPath);
    if (!fs.existsSync(dbDir)) {
        fs.mkdirSync(dbDir, { recursive: true });
    }

    const db = new DatabaseSync(dbPath);

    // Schema initialization
    db.exec(`
        PRAGMA journal_mode = DELETE;
        PRAGMA busy_timeout = 5000;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE IF NOT EXISTS whatsapp_sessions (
            user_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'disconnected',
            qr_code TEXT,
            phone TEXT,
            push_name TEXT,
            last_sync TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS whatsapp_contacts (
            user_id TEXT NOT NULL,
            jid TEXT NOT NULL,
            number TEXT,
            lid TEXT,
            name TEXT,
            push_name TEXT,
            profile_pic_url TEXT,
            is_group INTEGER DEFAULT 0,
            unread_count INTEGER DEFAULT 0,
            last_message_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, jid)
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_number ON whatsapp_contacts (user_id, number);

        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            sender TEXT,
            from_me INTEGER NOT NULL DEFAULT 0,
            type TEXT NOT NULL DEFAULT 'text',
            body TEXT,
            media_url TEXT,
            media_mime TEXT,
            media_filename TEXT,
            timestamp INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'received',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_wam_user_chat ON whatsapp_messages (user_id, chat_id, timestamp);
    `);

    // Migration safe check for lid column
    try {
        const cols = db.prepare("PRAGMA table_info(whatsapp_contacts)").all().map(c => c.name);
        if (!cols.includes('lid')) {
            db.exec("ALTER TABLE whatsapp_contacts ADD COLUMN lid TEXT;");
        }
        db.exec("CREATE INDEX IF NOT EXISTS idx_contacts_lid ON whatsapp_contacts (user_id, lid);");
        if (!cols.includes('media_filename')) {
            const mcols = db.prepare("PRAGMA table_info(whatsapp_messages)").all().map(c => c.name);
            if (!mcols.includes('media_filename')) {
                db.exec("ALTER TABLE whatsapp_messages ADD COLUMN media_filename TEXT;");
            }
        }
    } catch (migErr) {}

    // Helper functions attached to db object
    db.saveSession = (userId, status, qrCode = null, phone = null, pushName = null) => {
        const stmt = db.prepare(`
            INSERT INTO whatsapp_sessions (user_id, status, qr_code, phone, push_name, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                status = excluded.status,
                qr_code = excluded.qr_code,
                phone = COALESCE(excluded.phone, whatsapp_sessions.phone),
                push_name = COALESCE(excluded.push_name, whatsapp_sessions.push_name),
                updated_at = CURRENT_TIMESTAMP
        `);
        stmt.run(String(userId), status, qrCode, phone, pushName);
    };

    db.getSession = (userId) => {
        const stmt = db.prepare('SELECT * FROM whatsapp_sessions WHERE user_id = ?');
        return stmt.get(String(userId)) || null;
    };

    db.upsertContact = (userId, contact) => {
        // Ignora grupos do WhatsApp
        if (contact.is_group || contact.jid?.includes('@g.us')) {
            return;
        }

        const stmt = db.prepare(`
            INSERT INTO whatsapp_contacts (
                user_id, jid, number, lid, name, push_name, profile_pic_url,
                is_group, unread_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, jid) DO UPDATE SET
                number = COALESCE(excluded.number, whatsapp_contacts.number),
                lid = COALESCE(excluded.lid, whatsapp_contacts.lid),
                name = CASE 
                    WHEN excluded.name IS NOT NULL AND excluded.name != '' AND NOT excluded.name LIKE '%@%'
                    THEN excluded.name 
                    ELSE whatsapp_contacts.name 
                END,
                push_name = COALESCE(excluded.push_name, whatsapp_contacts.push_name),
                profile_pic_url = COALESCE(excluded.profile_pic_url, whatsapp_contacts.profile_pic_url),
                unread_count = excluded.unread_count,
                updated_at = CURRENT_TIMESTAMP
        `);
        stmt.run(
            String(userId),
            contact.jid,
            contact.number || null,
            contact.lid || null,
            contact.name || null,
            contact.push_name || null,
            contact.profile_pic_url || null,
            contact.is_group ? 1 : 0,
            contact.unread_count || 0
        );
    };

    db.findContact = (userId, identifier) => {
        if (!identifier) return null;
        const clean = identifier.toString().replace(/\D/g, '');
        const cleanJid = identifier.includes('@') ? identifier : `${clean}@c.us`;

        try {
            const stmt = db.prepare(`
                SELECT * FROM whatsapp_contacts
                WHERE user_id = ? AND (
                    jid = ? OR 
                    number = ? OR 
                    lid = ? OR 
                    lid LIKE ? OR
                    number LIKE ?
                )
                LIMIT 1
            `);
            return stmt.get(
                String(userId),
                cleanJid,
                clean,
                identifier,
                `%${clean}%`,
                `%${clean}%`
            ) || null;
        } catch (err) {
            console.warn('Aviso em findContact:', err.message);
            return null;
        }
    };

    db.getContact = (userId, jid) => {
        return db.findContact(userId, jid);
    };

    db.getContacts = (userId) => {
        // Retorna apenas pessoas (exclui grupos e @lid sem nome)
        const stmt = db.prepare(`
            SELECT * FROM whatsapp_contacts
            WHERE user_id = ? 
              AND is_group = 0 
              AND NOT jid LIKE '%@g.us'
              AND NOT (name LIKE '%@lid' AND number LIKE '%@lid')
            ORDER BY 
              CASE WHEN name IS NOT NULL AND NOT name LIKE '%@%' THEN 0 ELSE 1 END,
              name ASC
        `);
        return stmt.all(String(userId));
    };

    db.saveMessage = (msg) => {
        const stmt = db.prepare(`
            INSERT OR IGNORE INTO whatsapp_messages (
                id, user_id, chat_id, sender, from_me, type, body,
                media_url, media_mime, media_filename, timestamp, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `);
        stmt.run(
            msg.id,
            String(msg.user_id),
            msg.chat_id,
            msg.sender || null,
            msg.from_me ? 1 : 0,
            msg.type || 'text',
            msg.body || '',
            msg.media_url || null,
            msg.media_mime || null,
            msg.media_filename || null,
            msg.timestamp || Math.floor(Date.now() / 1000),
            msg.status || 'received'
        );
    };

    db.getChatMessages = (userId, chatId, limit = 100) => {
        const stmt = db.prepare(`
            SELECT * FROM whatsapp_messages
            WHERE user_id = ? AND (chat_id = ? OR chat_id LIKE ?)
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
        `);
        const cleanDigits = chatId.replace(/\D/g, '');
        return stmt.all(String(userId), chatId, `%${cleanDigits}%`, limit);
    };

    db.markChatAsRead = (userId, chatId) => {
        const stmt = db.prepare(`
            UPDATE whatsapp_contacts
            SET unread_count = 0
            WHERE user_id = ? AND (jid = ? OR number = ? OR lid = ?)
        `);
        stmt.run(String(userId), chatId, chatId.replace(/\D/g, ''), chatId);
    };

    instance = db;
    return db;
}

function getDb() {
    if (!instance) {
        return initDatabase();
    }
    return instance;
}

module.exports = {
    initDatabase,
    getDb
};
