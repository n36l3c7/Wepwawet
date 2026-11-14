import { test } from 'node:test'
import assert from 'node:assert/strict'
import { exportUrl, parseTargets } from './api.js'

test('target lists drop comments, blanks and duplicates', () => {
  assert.deepEqual(parseTargets('1.1.1.1\n# office\n\n ripe.net , \n1.1.1.1\r\n2001:db8::1 # doc'), [
    '1.1.1.1',
    'ripe.net',
    '2001:db8::1',
  ])
})

test('export urls', () => {
  assert.equal(exportUrl('abc', 'kml'), '/api/runs/abc/export?format=kml')
})
