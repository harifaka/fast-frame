// Delivery editions reported by GET /api/system/config.
//
// lite     - no AI runtime (no YOLO tracking, point clouds or horizon AI)
// standard - CPU AI (YOLO11s + MiDaS), full interface
// pro      - GPU AI (YOLO11m + Depth Anything V2), full interface

export const EDITION_LITE = 'lite'
export const EDITION_STANDARD = 'standard'
export const EDITION_PRO = 'pro'

const KNOWN_EDITIONS = new Set([EDITION_LITE, EDITION_STANDARD, EDITION_PRO])

export function normalizeEdition(raw) {
  const value = String(raw || '')
    .trim()
    .toLowerCase()
  return KNOWN_EDITIONS.has(value) ? value : EDITION_STANDARD
}

export function isLiteEdition(raw) {
  return normalizeEdition(raw) === EDITION_LITE
}
