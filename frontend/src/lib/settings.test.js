import { test } from 'node:test'
import assert from 'node:assert/strict'
import { changedValues, keyState, sshHostsPayload } from './settings.js'

test('changedValues keeps only what changed', () => {
  const original = { map: { basemap: 'carto' }, providers: { shodan: { enabled: true }, rdns: { enabled: true } } }
  const edited = { map: { basemap: 'carto' }, providers: { shodan: { enabled: false }, rdns: { enabled: true } } }
  assert.deepEqual(changedValues(original, edited), { providers: { shodan: { enabled: false } } })
  assert.deepEqual(changedValues(original, original), {})
})

test('keyState names the source without the value', () => {
  assert.equal(keyState({ source: 'env', variable: 'WEPWAWET_SHODAN' }), 'set by WEPWAWET_SHODAN')
  assert.equal(keyState({ source: 'file' }), 'set')
  assert.equal(keyState({ source: null }), 'not set')
})

test('SSH rows become hosts: blanks dropped, numbers as numbers', () => {
  const rows = [
    { name: ' lab ', host: ' 192.0.2.7 ', user: '', port: '2222', lat: '45.4', lon: '9.2' },
    { name: '', host: '  ', user: '', port: '', lat: '', lon: '' },
    { name: '', host: 'vps.example.org', user: 'me', port: '', lat: '', lon: '8.5' },
  ]
  assert.deepEqual(sshHostsPayload(rows), [
    { host: '192.0.2.7', name: 'lab', port: 2222, lat: 45.4, lon: 9.2 },
    { host: 'vps.example.org', user: 'me', lat: null, lon: 8.5 },
  ])
})

test('changing one source option sends the whole source', () => {
  const original = { providers: { ripe_atlas: { enabled: true, max_credits: 0 } } }
  const edited = { providers: { ripe_atlas: { enabled: true, max_credits: 600 } } }
  assert.deepEqual(changedValues(original, edited), { providers: { ripe_atlas: { enabled: true, max_credits: 600 } } })
})
