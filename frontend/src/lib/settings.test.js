import { test } from 'node:test'
import assert from 'node:assert/strict'
import { changedValues, keyState } from './settings.js'

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
