import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isAllowed, normalizeJid, parseAllowed } from './allowlist.mjs'

test('without a list every sender is allowed', () => {
  assert.equal(isAllowed(parseAllowed(''), '5355161759@s.whatsapp.net'), true)
  assert.equal(isAllowed(parseAllowed(undefined), '999@lid'), true)
})

test('a number matches with or without the country code', () => {
  const entries = parseAllowed('55161759')
  assert.equal(isAllowed(entries, '5355161759@s.whatsapp.net'), true)
  assert.equal(isAllowed(entries, '55161759@s.whatsapp.net'), true)
  assert.equal(isAllowed(parseAllowed('+53 5516 1759'), '5355161759@s.whatsapp.net'), true)
})

test('other numbers are rejected', () => {
  const entries = parseAllowed('55161759')
  assert.equal(isAllowed(entries, '5355161750@s.whatsapp.net'), false)
  assert.equal(isAllowed(entries, '34600000000@s.whatsapp.net'), false)
})

test('the device suffix is ignored', () => {
  assert.equal(isAllowed(parseAllowed('55161759'), '5355161759:12@s.whatsapp.net'), true)
  assert.equal(normalizeJid('5355161759:12@S.whatsapp.net'), '5355161759@s.whatsapp.net')
})

test('digits never match groups or lid ids', () => {
  const entries = parseAllowed('55161759')
  assert.equal(isAllowed(entries, '120055161759@g.us'), false)
  assert.equal(isAllowed(entries, '8855161759@lid'), false)
})

test('an exact id can be listed to allow a lid sender', () => {
  const entries = parseAllowed('55161759, 8855161759@lid')
  assert.equal(isAllowed(entries, '8855161759@lid'), true)
  assert.equal(isAllowed(entries, '8855161758@lid'), false)
})

test('a missing id is rejected when a list is set', () => {
  assert.equal(isAllowed(parseAllowed('55161759'), undefined), false)
})

test('an unusable list fails instead of allowing everyone', () => {
  assert.throws(() => parseAllowed('abc'))
  assert.throws(() => parseAllowed('123'))
  assert.throws(() => parseAllowed('55161759, 12'))
})
