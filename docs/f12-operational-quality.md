# Calidad operacional F12

`f12-operational-quality-v1` es una politica conservadora del backend aplicada
al crear una `f12_evidence_preparation`. No cambia el contrato F12 ni llama a
Analytics. La decision queda congelada en
`f12_evidence_preparations.audit.operational_quality` con version, resultado,
codigos de motivo, comprobaciones, timestamp UTC y bindings a captura,
preparacion, perfil, dispositivo, fecha y revision.

Un dia solo obtiene `quality_verified=true` cuando los originales persistidos
vuelven a superar las comprobaciones de hashes, tamanos, conteos, asociacion de
consultas/eventos y binding autenticado; existe estado de pantalla anterior al
inicio; consultas exitosas cubren continuamente desde ese estado hasta el fin
del dia; no hay contradicciones ni eventos de ciclo de vida pendientes; la
representacion preparada es `OBSERVED` u `OBSERVED_ZERO`; y la revision 1 es
inequivoca.

La captura fuente (`source_created_at`, procedente del manifiesto Android) debe
ocurrir durante el dia local siguiente al representado. `received_at` registra
el envio al backend y puede ser posterior sin invalidar la decision. Los
timestamps se conservan en UTC y se interpretan con la zona IANA vinculada a la
serie.

Una consulta auxiliar fallida no invalida por si sola un dia cuando otras
consultas exitosas acreditan toda la cobertura necesaria. Una consulta vacia no
prueba uso cero sin estado inicial y cobertura continua. El resultado C1,
incluido `ABSTAIN`, no participa en esta politica.

`AdultMessageWindowService` solo acepta una decision si la version y todos sus
bindings coinciden con la captura y preparacion seleccionadas. Las correcciones,
series ambiguas, evidencia faltante, calidad rechazada, declaraciones adultas
incompletas o consentimiento historico incompleto mantienen
`evaluation_ready=false`.

Codigos de rechazo principales:

- `ORIGINAL_INTEGRITY_INVALID`
- `AUTHENTICATED_BINDING_INVALID`
- `SOURCE_CAPTURE_OUTSIDE_NEXT_LOCAL_DAY`
- `INITIAL_SCREEN_STATE_UNKNOWN`
- `CONTINUOUS_QUERY_COVERAGE_INCOMPLETE`
- `CONTRADICTORY_SCREEN_STATES`
- `DEVICE_LIFECYCLE_UNRESOLVED`
- `RECONSTRUCTION_NOT_VERIFIABLE`
- `CORRECTION_POLICY_PENDING`

La politica no acredita exhaustividad fisica absoluta de `UsageEvents` ni
autoriza por si sola una evaluacion semanal. Tampoco define reconciliacion para
revisiones mayores a 1.
