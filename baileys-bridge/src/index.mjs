import makeWASocket, { DisconnectReason, useMultiFileAuthState } from '@whiskeysockets/baileys'
import qrcode from 'qrcode-terminal'
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, readdir, rename, unlink, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { isAllowed, parseAllowed } from './allowlist.mjs'
import { createClient } from './django-client.mjs'

const baseUrl = (process.env.DJANGO_INTERNAL_URL || '').replace(/\/$/, '')
const channelId = process.env.BAILEYS_CHANNEL_ID || ''
const secret = process.env.BAILEYS_CHANNEL_SECRET || ''
const publicHost = process.env.DJANGO_PUBLIC_HOST || 'localhost'
const authDir = process.env.BAILEYS_AUTH_DIR || '/data/auth'
const dataDir = process.env.BAILEYS_DATA_DIR || '/data'
const inboxDir = join(dataDir, 'inbox')
const sentDir = join(dataDir, 'sent')
if (!baseUrl || !channelId || !secret) {
  throw new Error('DJANGO_INTERNAL_URL, BAILEYS_CHANNEL_ID and BAILEYS_CHANNEL_SECRET are required')
}
const allowedSenders = parseAllowed(process.env.BAILEYS_ALLOWED_NUMBERS)
if (allowedSenders.length === 0) {
  console.warn('BAILEYS_ALLOWED_NUMBERS is not set: every private chat will receive replies')
}
const ignoredSenders = new Set()
const endpoint = `${baseUrl}/channels/baileys/${channelId}`
const api = createClient({ endpoint, secret, publicHost })
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
let socket
let connected = false
let starting = false
const inboundQueue = []

async function atomicJson(path, value) {
  const temporary = `${path}.${randomUUID()}.tmp`
  await writeFile(temporary, JSON.stringify(value))
  await rename(temporary, path)
}

async function loadPending() {
  await mkdir(inboxDir, { recursive: true })
  await mkdir(sentDir, { recursive: true })
  for (const name of await readdir(inboxDir)) {
    if (!name.endsWith('.json')) continue
    const path = join(inboxDir, name)
    try {
      inboundQueue.push({ path, payload: JSON.parse(await readFile(path, 'utf8')) })
    } catch (error) {
      console.error('Unreadable inbound spool entry:', name, error.message)
    }
  }
}

async function enqueueInbound(payload) {
  const name = createHash('sha256').update(`${payload.chat_id}:${payload.message_id}`).digest('hex')
  const path = join(inboxDir, `${name}.json`)
  await atomicJson(path, payload)
  if (!inboundQueue.some(item => item.path === path)) inboundQueue.push({ path, payload })
}

function textOf(message) {
  const content = message.message?.ephemeralMessage?.message || message.message
  return content?.conversation || content?.extendedTextMessage?.text || ''
}

async function flushInbound() {
  while (true) {
    if (inboundQueue.length === 0) {
      await delay(500)
      continue
    }
    try {
      await api('/inbound/', 'POST', inboundQueue[0].payload)
      await unlink(inboundQueue[0].path)
      inboundQueue.shift()
    } catch (error) {
      console.error('Inbound delivery failed:', error.message)
      await delay(5000)
    }
  }
}

async function deliverOutbound() {
  while (true) {
    if (!connected || !socket) {
      await delay(1000)
      continue
    }
    try {
      const { delivery } = await api('/outbound/')
      if (!delivery) {
        await delay(1500)
        continue
      }
      const receiptPath = join(sentDir, `${delivery.id}.json`)
      let providerMessageId
      try {
        providerMessageId = JSON.parse(await readFile(receiptPath, 'utf8')).provider_message_id
      } catch (error) {
        if (error.code !== 'ENOENT') throw error
        const result = await socket.sendMessage(delivery.chat_id, { text: delivery.content })
        providerMessageId = result?.key?.id || ''
        await atomicJson(receiptPath, { provider_message_id: providerMessageId })
      }
      await api(`/outbound/${delivery.id}/ack/`, 'POST', {
        lease_token: delivery.lease_token,
        provider_message_id: providerMessageId
      })
      await unlink(receiptPath)
    } catch (error) {
      console.error('Outbound delivery failed:', error.message)
      await delay(5000)
    }
  }
}

async function connect() {
  if (starting) return
  starting = true
  try {
    const { state, saveCreds } = await useMultiFileAuthState(authDir)
    const nextSocket = makeWASocket({
      auth: state,
      markOnlineOnConnect: false,
      shouldSyncHistoryMessage: () => false
    })
    socket = nextSocket
    nextSocket.ev.on('creds.update', saveCreds)
    nextSocket.ev.on('messages.upsert', ({ type, messages, requestId }) => {
      if (type !== 'notify' || requestId) return
      for (const message of messages) {
        const jid = message.key?.remoteJid
        const id = message.key?.id
        if (message.key?.fromMe || !jid || !(jid.endsWith('@s.whatsapp.net') || jid.endsWith('@lid')) || !id) continue
        if (!isAllowed(allowedSenders, jid)) {
          if (!ignoredSenders.has(jid)) {
            ignoredSenders.add(jid)
            console.log(`Ignored message from a sender that is not in BAILEYS_ALLOWED_NUMBERS: ${jid}`)
          }
          continue
        }
        const content = textOf(message).trim()
        if (!content || content.length > 4000 || content.includes('\x00')) continue
        enqueueInbound({ chat_id: jid, message_id: id, content }).catch(console.error)
      }
    })
    nextSocket.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
      if (qr) qrcode.generate(qr, { small: true })
      if (connection === 'open') {
        connected = true
        console.log('WhatsApp connected')
      } else if (connection === 'close') {
        connected = false
        if (socket === nextSocket) socket = undefined
        const status = lastDisconnect?.error?.output?.statusCode
        if (status === DisconnectReason.loggedOut) {
          console.error('WhatsApp session logged out; re-pair this channel')
        } else {
          setTimeout(() => connect().catch(console.error), 5000)
        }
      }
    })
  } finally {
    starting = false
  }
}

await loadPending()
flushInbound().catch(console.error)
deliverOutbound().catch(console.error)
connect().catch(error => {
  console.error(error)
  process.exit(1)
})
