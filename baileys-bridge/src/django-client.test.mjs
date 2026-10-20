import assert from 'node:assert/strict'
import http from 'node:http'
import { after, before, test } from 'node:test'
import { createClient } from './django-client.mjs'

let server
let port
let seen

before(async () => {
  server = http.createServer((req, res) => {
    const chunks = []
    req.on('data', chunk => chunks.push(chunk))
    req.on('end', () => {
      seen = { method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString('utf8') }
      if (req.url.endsWith('/missing/')) return res.writeHead(404).end()
      if (req.url.endsWith('/empty/')) return res.writeHead(204).end()
      if (req.url.endsWith('/slow/')) return setTimeout(() => res.end('{}'), 300)
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ ok: true }))
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  port = server.address().port
})

after(() => server.close())

const client = (options = {}) =>
  createClient({ endpoint: `http://127.0.0.1:${port}/channels/baileys/abc`, secret: 's3cret', publicHost: 'healthcheck.railway.app', ...options })

test('sends the configured Host header and the bearer secret', async () => {
  await client()('/outbound/')
  assert.equal(seen.headers.host, 'healthcheck.railway.app')
  assert.equal(seen.headers.authorization, 'Bearer s3cret')
  assert.equal(seen.url, '/channels/baileys/abc/outbound/')
})

test('posts a JSON body and parses the JSON response', async () => {
  const result = await client()('/inbound/', 'POST', { chat_id: 'x@lid', content: 'hola ñ' })
  assert.deepEqual(result, { ok: true })
  assert.equal(seen.method, 'POST')
  assert.equal(seen.headers['content-type'], 'application/json')
  assert.deepEqual(JSON.parse(seen.body), { chat_id: 'x@lid', content: 'hola ñ' })
})

test('returns null for 204 responses', async () => {
  assert.equal(await client()('/empty/', 'POST', {}), null)
})

test('rejects non-2xx responses with the status code', async () => {
  await assert.rejects(client()('/missing/'), /HTTP 404/)
})

test('rejects when the server is too slow', async () => {
  await assert.rejects(client({ timeoutMs: 50 })('/slow/'), /timed out/)
})
