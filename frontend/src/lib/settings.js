/** Options of the settings panel: labels, input kinds and the changes to send. */

export const OPTION_GROUPS = [
  {
    title: 'Map and window',
    options: [
      { section: 'map', key: 'basemap', label: 'Base map at start-up', choices: ['carto', 'offline'] },
      {
        section: 'gui',
        key: 'rendering',
        label: 'Window rendering',
        choices: ['auto', 'software', 'hardware'],
        help: 'software draws the map on the CPU; auto does so in virtual machines and without a GPU.',
      },
      { section: 'logging', key: 'level', label: 'Log level', choices: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
      {
        section: 'cache',
        key: 'default_ttl_seconds',
        label: 'Cache lifetime of passive answers (seconds)',
        type: 'int',
        min: 1,
      },
    ],
  },
  {
    title: 'Measurements',
    options: [
      { section: 'measurements', key: 'enabled', label: 'Run measurements', type: 'bool' },
      { section: 'measurements', key: 'max_tests', label: 'Most tests per target', type: 'int', min: 0 },
      { section: 'measurements', key: 'sweep_probes', label: 'Global sweep probes', type: 'int', min: 0, max: 500 },
      { section: 'measurements', key: 'zoom_probes', label: 'Regional zoom probes', type: 'int', min: 0, max: 500 },
      {
        section: 'measurements',
        key: 'local_probes',
        label: 'Local zoom probes (city and network)',
        type: 'int',
        min: 0,
        max: 500,
      },
      { section: 'measurements', key: 'packets', label: 'Packets per ping', type: 'int', min: 1, max: 16 },
      {
        section: 'measurements',
        key: 'speed_km_per_ms',
        label: 'Signal speed (km per ms, one way)',
        type: 'float',
        min: 51,
        max: 300,
        help: '200 is light in fibre; lower only for testing.',
      },
      { section: 'measurements', key: 'traceroute_probes', label: 'Traceroutes', type: 'int', min: 0, max: 20 },
      {
        section: 'measurements',
        key: 'tcp_fallback_port',
        label: 'TCP fallback port (0 = off)',
        type: 'int',
        min: 0,
        max: 65535,
      },
      { section: 'measurements', key: 'access_correction', label: 'Remove the access delay', type: 'bool' },
      {
        section: 'measurements',
        key: 'calibrate',
        label: 'CBG calibration (RIPE Atlas anchors)',
        type: 'bool',
        help: 'The nearest probes also ping anchors around the region: tighter bounds, one test per probe and anchor.',
      },
      {
        section: 'measurements',
        key: 'calibration_anchors',
        label: 'Calibration anchors',
        type: 'int',
        min: 1,
        max: 20,
      },
      {
        section: 'measurements',
        key: 'landmarks',
        label: 'Landmarks, experimental (0 = off)',
        type: 'int',
        min: 0,
        max: 20,
        help: 'RIPE Atlas probes in the region, pinged from the nearest probes: each costs one test per probe.',
      },
    ],
  },
]

/** Options of single sources, shown under their name in the Sources section. */
export const PROVIDER_OPTIONS = {
  ripe_atlas: [
    {
      key: 'max_credits',
      label: 'Most credits per target (0 = off)',
      type: 'int',
      min: 0,
      help: 'A ping costs 6 credits per probe; nothing is spent while this is 0.',
    },
  ],
  rdns: [{ key: 'sweep', label: 'Also look up the 254 neighbours in the /24', type: 'bool' }],
  local: [
    { key: 'lat', label: 'Latitude of this machine', type: 'float', min: -90, max: 90, nullable: true },
    { key: 'lon', label: 'Longitude of this machine', type: 'float', min: -180, max: 180, nullable: true },
  ],
}

/** An empty SSH host row for the editor. */
export function emptySshHost() {
  return { name: '', host: '', user: '', port: '', lat: '', lon: '' }
}

/** SSH host rows as the API expects them: blanks dropped, numbers as numbers. */
export function sshHostsPayload(rows) {
  return rows
    .filter((row) => String(row.host ?? '').trim())
    .map((row) => {
      const host = { host: String(row.host).trim() }
      for (const key of ['name', 'user']) {
        if (String(row[key] ?? '').trim()) host[key] = String(row[key]).trim()
      }
      if (row.port !== '' && row.port !== null && row.port !== undefined) host.port = Number(row.port)
      host.lat = row.lat === '' ? null : Number(row.lat)
      host.lon = row.lon === '' ? null : Number(row.lon)
      return host
    })
}

/** Only the options that differ from `original`, in the shape the API expects. */
export function changedValues(original, edited) {
  const changes = {}
  for (const [section, values] of Object.entries(edited)) {
    for (const [key, value] of Object.entries(values)) {
      const before = original?.[section]?.[key]
      const same =
        value && typeof value === 'object' ? JSON.stringify(value) === JSON.stringify(before) : value === before
      if (!same) changes[section] = { ...changes[section], [key]: value }
    }
  }
  return changes
}

/** Where a key comes from, in words. */
export function keyState(key) {
  if (key.source === 'env') return `set by ${key.variable}`
  if (key.source === 'file') return 'set'
  return 'not set'
}
