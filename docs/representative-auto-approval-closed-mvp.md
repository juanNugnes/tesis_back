# Autoaprobacion acotada para pruebas cerradas del MVP

Esta funcion solo evita la aprobacion manual de dos cuentas de prueba conocidas.
OTP demuestra control del correo y el codigo de vinculacion demuestra posesion
temporal del codigo; ninguno acredita identidad, tutela o representacion legal.
No habilitarla para familias fuera del grupo cerrado.

## Configuracion en la instancia AWS

1. Identificar las dos direcciones completas de prueba fuera del repositorio.
   Deben ser diferentes tras quitar espacios externos y pasar a minusculas. No usar
   dominios, patrones ni direcciones parciales.
2. En el archivo de entorno protegido que carga **backend** en EC2, establecer:

   ```text
   AUTO_APPROVE_REPRESENTATIVE_VERIFICATION=true
   AUTO_APPROVE_REPRESENTATIVE_EMAIL_ALLOWLIST=<correo-prueba-1>,<correo-prueba-2>
   ```

   Sustituir ambos marcadores por las direcciones reales solo en EC2. No
   commitear el archivo ni imprimirlo mediante `docker compose config`, `env`,
   `docker inspect` o logs. No se requieren cambios en Android, Caddy, IAM o
   PostgreSQL.
3. Publicar una imagen backend revisada y recrear **solo backend** con el
   procedimiento de despliegue vigente. Si se usa el Compose del repositorio:

   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.analytics.yml config --quiet
   docker compose -f docker-compose.yml -f docker-compose.analytics.yml up -d --no-deps --build backend
   docker compose -f docker-compose.yml -f docker-compose.analytics.yml ps
   ```

   Verificar antes que ese Compose sea el usado realmente en AWS; los comandos
   anteriores son una plantilla, no una instruccion de despliegue automatico.
4. Probar con un correo autorizado: vinculo activo y verificacion pendiente,
   OTP valido, estado APPROVED y un solo AuditEvent con
   `verified_by=automatic-mvp-allowlist`. Comprobar que Consent sigue pendiente
   hasta la aceptacion explicita del aviso. Probar tambien un tercer correo:
   debe continuar PENDING. No incluir OTP, JWT ni correos en evidencias o logs.
5. Al terminar las pruebas, poner el flag en `false` y retirar la lista del
   entorno. Una verificacion ya aprobada no se revierte automaticamente; una
   revocacion o revision posterior requiere el procedimiento manual vigente.

Una lista vacia, con una sola direccion, con duplicados, patrones o entradas
invalidas no habilita ninguna aprobacion aunque el flag sea `true`. El OTP
solo recorre vinculos activos de ese adulto con perfiles activos. El claim
adulto conserva sus controles de codigo, perfil y vinculo revocado. No se
crean vinculos ni consentimientos durante la autoaprobacion.
