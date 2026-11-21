# Recepción backend de evidencia original F12

## Alcance

`POST /api/v1/profiles/{profile_id}/f12-evidence/v1` recibe y conserva evidencia,
pero no ejecuta `f12_export.py`, no crea `historical_submissions` y no llama a
Analytics. Requiere `DeviceBearer`; el dispositivo debe estar activo, pertenecer
al perfil del path y tener autorización vigente de `USAGE_MONITORING`.

El body es `multipart/form-data` con `Content-Length` obligatorio:

- una parte de texto `manifest` con JSON estricto;
- una parte de archivo `summary` por consulta;
- una parte de archivo `events` por consulta.

Las partes `summary` y `events` se emparejan por posición con `manifest.pairs`.
Sus nombres físicos no son autoridad. El máximo es 48 MiB para el multipart y
32 MiB para la suma de bytes fuente. Cada par conserva exactamente sus bytes
originales en PostgreSQL; el backend no los normaliza ni reserializa.

## Manifiesto disponible

```json
{
  "envelope_version": 1,
  "capture_id": "<sha256>",
  "adapter_version": "f12-android-export-adapter-v1-candidate",
  "local_date": "2026-11-18",
  "time_zone": "America/Argentina/Buenos_Aires",
  "revision": 1,
  "created_at_epoch_millis": 1795053720000,
  "content_sha256": "<sha256>",
  "evidence_state": "AVAILABLE",
  "unavailable_reason": null,
  "pairs": [
    {
      "collector_run_id": "e12f9120-7641-4ae2-a988-d6c43d4cfd17",
      "summary_sha256": "<sha256 de summary>",
      "events_sha256": "<sha256 de events>"
    }
  ]
}
```

`content_sha256` reproduce el algoritmo Android: por cada par, en el orden del
manifiesto, agrega `summaryBytes`, un byte cero, `eventsBytes` y un byte cero a un
SHA-256 incremental.

El binding no acepta IDs declarados dentro del manifiesto. El backend calcula:

```text
capture_id = SHA256(profile_id + NUL + device_id + NUL + local_date + NUL
                    + revision + NUL + content_sha256)
```

con los UUID obtenidos del token autenticado. La colección debe incluir una
consulta cuyos límites coincidan exactamente con el día local declarado. Las
consultas anteriores necesarias para estado previo se envían como pares
adicionales.

## Evidencia no exportada

No exportar no equivale a una consulta exitosa con cero eventos. Android debe
enviar un manifiesto sin partes de archivo:

```json
{
  "envelope_version": 1,
  "capture_id": "<sha256 del binding>",
  "adapter_version": "f12-android-export-adapter-v1-candidate",
  "local_date": "2026-11-18",
  "time_zone": "America/Argentina/Buenos_Aires",
  "revision": 1,
  "created_at_epoch_millis": 1795053720000,
  "content_sha256": "<sha256 del marcador>",
  "evidence_state": "UNAVAILABLE",
  "unavailable_reason": "NOT_EXPORTED",
  "pairs": []
}
```

El hash del marcador se calcula sobre los bytes ASCII
`modera-f12-unavailable-v1`, un byte cero y `NOT_EXPORTED`. Ese hash se utiliza
en la fórmula normal de `capture_id`. Este registro nunca debe entregarse como
un par vacío a `f12_export.py`.

## Recibo e idempotencia

Una creación responde `201` y un reintento idéntico `200`. Ambos devuelven el
mismo body persistido:

```json
{
  "receipt_id": "<uuid backend>",
  "capture_id": "<sha256>",
  "status": "RECEIVED",
  "evidence_state": "AVAILABLE",
  "local_date": "2026-11-18",
  "revision": 1,
  "content_sha256": "<sha256>",
  "pair_count": 1,
  "total_bytes": 1234,
  "received_at": "<UTC datetime>",
  "expires_at": null
}
```

`X-F12-Evidence-Write-Result` distingue `created` de `unchanged`. Reutilizar un
`capture_id` con otro contenido o una revisión existente con otros bytes devuelve
`409`. Una corrección debe incrementar la revisión y conserva la anterior.

## Persistencia y privacidad

Las tablas `f12_raw_evidence_captures` y `f12_raw_evidence_pairs` se escriben en
una única transacción. Los blobs no tienen endpoint de lectura ni aparecen en
logs de aplicación. Las FK eliminan en cascada al suprimir perfil o dispositivo.

`F12_EVIDENCE_RETENTION_DAYS` puede establecer expiración y limpieza oportunista
en la siguiente recepción del perfil. Su valor predeterminado es `null` porque el
proyecto todavía no aprobó un plazo de retención automático. Antes de producción
debe confirmarse el plazo y verificarse cifrado en reposo, backups y política de
supresión de PostgreSQL/RDS.
