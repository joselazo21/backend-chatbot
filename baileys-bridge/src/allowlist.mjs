// Sender allowlist for the bridge. Entries are phone numbers (digits, optional "+" and spaces)
// or exact WhatsApp identifiers such as "123456789@lid".

const MIN_DIGITS = 7

// "5355161759:12@s.whatsapp.net" -> "5355161759@s.whatsapp.net" (drop the device suffix)
export function normalizeJid(jid) {
  const [user, server = ''] = String(jid).toLowerCase().split('@')
  return `${user.split(':')[0]}@${server}`
}

export function parseAllowed(value = '') {
  const items = String(value).split(',').map(item => item.trim()).filter(Boolean)
  const entries = items.map(item => {
    if (item.includes('@')) return { jid: normalizeJid(item) }
    return { digits: item.replace(/\D/g, '') }
  })
  // A value that is set but unusable must never fall back to "allow everyone".
  if (items.length > 0 && entries.some(entry => !entry.jid && entry.digits.length < MIN_DIGITS)) {
    throw new Error(`BAILEYS_ALLOWED_NUMBERS entries need at least ${MIN_DIGITS} digits or a full WhatsApp id`)
  }
  return entries
}

// A phone entry matches by suffix, so the country code is optional. Only direct phone chats
// (@s.whatsapp.net) can match by digits; any other id must be listed exactly.
export function isAllowed(entries, jid) {
  if (entries.length === 0) return true
  if (!jid) return false
  const normalized = normalizeJid(jid)
  const [user, server] = normalized.split('@')
  return entries.some(entry =>
    entry.jid ? normalized === entry.jid : server === 's.whatsapp.net' && user.endsWith(entry.digits)
  )
}
