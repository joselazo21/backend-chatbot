// HTTP client for the Django channel API. It uses node:http(s) instead of fetch because fetch
// silently ignores a custom Host header, and Django resolves the tenant from that header.
import http from 'node:http'
import https from 'node:https'

export function createClient({ endpoint, secret, publicHost, timeoutMs = 20000 }) {
  return function api(path, method = 'GET', body) {
    const url = new URL(`${endpoint}${path}`)
    const payload = body ? JSON.stringify(body) : undefined
    const secure = url.protocol === 'https:'
    return new Promise((resolve, reject) => {
      const request = (secure ? https : http).request(
        url,
        {
          method,
          timeout: timeoutMs,
          // Keep TLS verification tied to the address we dial, not to the Host override.
          ...(secure ? { servername: url.hostname } : {}),
          headers: {
            Authorization: `Bearer ${secret}`,
            Host: publicHost,
            ...(payload
              ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
              : {})
          }
        },
        response => {
          const chunks = []
          response.on('data', chunk => chunks.push(chunk))
          response.on('end', () => {
            const status = response.statusCode
            if (status < 200 || status >= 300) return reject(new Error(`Django returned HTTP ${status}`))
            if (status === 204) return resolve(null)
            try {
              resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')))
            } catch (error) {
              reject(error)
            }
          })
          response.on('error', reject)
        }
      )
      request.on('timeout', () => request.destroy(new Error('Django request timed out')))
      request.on('error', reject)
      if (payload) request.write(payload)
      request.end()
    })
  }
}
