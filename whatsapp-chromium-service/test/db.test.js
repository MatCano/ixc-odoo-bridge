const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { initDatabase, getDb } = require('../db');

test('SQLite Database Layer', async (t) => {
    const testDbPath = path.join(__dirname, 'test_whatsapp.db');
    if (fs.existsSync(testDbPath)) {
        fs.unlinkSync(testDbPath);
    }

    const db = initDatabase(testDbPath);

    await t.test('Initializes tables properly', () => {
        const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(r => r.name);
        assert.ok(tables.includes('whatsapp_sessions'), 'whatsapp_sessions table should exist');
        assert.ok(tables.includes('whatsapp_contacts'), 'whatsapp_contacts table should exist');
        assert.ok(tables.includes('whatsapp_messages'), 'whatsapp_messages table should exist');
    });

    await t.test('Manages user session state', () => {
        db.saveSession('user_5', 'qr_ready', 'data:image/png;base64,TESTQR');
        let session = db.getSession('user_5');
        assert.equal(session.user_id, 'user_5');
        assert.equal(session.status, 'qr_ready');
        assert.equal(session.qr_code, 'data:image/png;base64,TESTQR');

        db.saveSession('user_5', 'ready', null, '5511999998888', 'Mauricio Paixão');
        session = db.getSession('user_5');
        assert.equal(session.status, 'ready');
        assert.equal(session.qr_code, null);
        assert.equal(session.phone, '5511999998888');
        assert.equal(session.push_name, 'Mauricio Paixão');
    });

    await t.test('Saves and retrieves contacts', () => {
        db.upsertContact('user_5', {
            jid: '5511988887777@c.us',
            number: '5511988887777',
            name: 'Cliente Teste',
            push_name: 'Cliente',
            unread_count: 2
        });

        const contacts = db.getContacts('user_5');
        assert.equal(contacts.length, 1);
        assert.equal(contacts[0].jid, '5511988887777@c.us');
        assert.equal(contacts[0].name, 'Cliente Teste');
        assert.equal(contacts[0].unread_count, 2);
    });

    await t.test('Stores and retrieves messages maintaining temporal sequence and deduplication', () => {
        const msg1 = {
            id: 'WMSG_001',
            user_id: 'user_5',
            chat_id: '5511988887777@c.us',
            sender: '5511988887777@c.us',
            from_me: 0,
            type: 'text',
            body: 'Olá, gostaria de saber sobre a internet',
            timestamp: 1700000000,
            status: 'received'
        };

        const msg2 = {
            id: 'WMSG_002',
            user_id: 'user_5',
            chat_id: '5511988887777@c.us',
            sender: 'user_5',
            from_me: 1,
            type: 'text',
            body: 'Olá! Temos planos de 500 Mega.',
            timestamp: 1700000010,
            status: 'sent'
        };

        // Insert messages
        db.saveMessage(msg1);
        db.saveMessage(msg2);

        // Deduplication test: re-insert msg1 should be ignored without error
        db.saveMessage(msg1);

        const history = db.getChatMessages('user_5', '5511988887777@c.us');
        assert.equal(history.length, 2, 'Should have exactly 2 messages (no duplicates)');
        assert.equal(history[0].id, 'WMSG_001');
        assert.equal(history[1].id, 'WMSG_002');
    });

    await t.test('Supports multimedia message records (voice, image, video)', () => {
        const mediaMsg = {
            id: 'WMSG_MEDIA_AUDIO',
            user_id: 'user_5',
            chat_id: '5511988887777@c.us',
            sender: '5511988887777@c.us',
            from_me: 0,
            type: 'voice',
            body: '[Mensagem de voz]',
            media_url: '/media/voice_001.ogg',
            media_mime: 'audio/ogg',
            timestamp: 1700000020,
            status: 'received'
        };

        db.saveMessage(mediaMsg);
        const history = db.getChatMessages('user_5', '5511988887777@c.us');
        assert.equal(history.length, 3);
        const last = history[2];
        assert.equal(last.type, 'voice');
        assert.equal(last.media_mime, 'audio/ogg');
    });

    await t.test('Marks chat as read and clears unread_count', () => {
        db.markChatAsRead('user_5', '5511988887777@c.us');
        const contact = db.getContact('user_5', '5511988887777@c.us');
        assert.equal(contact.unread_count, 0);
    });

    // Cleanup
    db.close();
    if (fs.existsSync(testDbPath)) {
        fs.unlinkSync(testDbPath);
    }
});
