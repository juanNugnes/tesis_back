# OTP por Amazon SES

Antes de publicar los endpoints OTP, aplicar Alembic `20261019_0016`. La migración
agrega `otp_rate_limit_events`: guarda sólo HMAC de correo/IP y hora, sin modificar
los desafíos existentes. El backend aplica 3 solicitudes por correo y 10 por IP
en 15 minutos, con 60 segundos de espera por correo. La verificación admite
10 intentos por correo y 30 por IP en 15 minutos, además del máximo existente
por desafío. Los rechazos devuelven 429 y `Retry-After` en segundos. Los eventos
expirados se purgan al procesar una solicitud admitida.

Variables opcionales: `OTP_RATE_WINDOW_MINUTES`, `OTP_REQUEST_EMAIL_LIMIT`,
`OTP_REQUEST_IP_LIMIT`, `OTP_REQUEST_COOLDOWN_SECONDS`,
`OTP_VERIFY_EMAIL_LIMIT` y `OTP_VERIFY_IP_LIMIT`. Mantener los mismos valores
en todas las instancias.

El contenedor inicia Uvicorn con `--no-proxy-headers` para conservar la IP del
peer TCP. Configurar `OTP_TRUSTED_PROXY_CIDRS` con la IP exacta de Caddy que
conecta al backend (por ejemplo, una dirección `/32`); el backend sólo entonces
considera `X-Forwarded-For` y elige el último salto no confiable. Mantener el
puerto de backend inaccesible desde Internet y asegurar que Caddy sanee las
cabeceras recibidas. Sin proxies confiables configurados se limita la IP de
Caddy como una sola fuente; probar esta configuración antes de abrir la API.

El backend selecciona el proveedor con `OTP_EMAIL_PROVIDER`. En producción debe ser
`ses`; `development` sólo funciona con `ENVIRONMENT=development` y descarta el correo.
La ausencia de proveedor o remitente impide solicitar OTP (HTTP 503). `EXPOSE_DEV_OTP`
debe permanecer en `false` en producción. La respuesta de producción nunca contiene
el código. Los vencimientos y el máximo de intentos siguen siendo los configurados
por `ADULT_OTP_TTL_MINUTES` y `ADULT_OTP_MAX_ATTEMPTS`.

Variables para producción:

```
ENVIRONMENT=production
OTP_EMAIL_PROVIDER=ses
SES_REGION=us-east-1
SES_SENDER_EMAIL=verificado@dominio.example
SES_TIMEOUT_SECONDS=5
EXPOSE_DEV_OTP=false
```

Crear una identidad verificada de correo o dominio en Amazon SES en `us-east-1`,
completar la verificación y configurar DKIM si se usa un dominio. Mientras la cuenta
esté en el sandbox de SES, también deben estar verificados los destinatarios; solicitar
acceso de producción en SES antes de admitir correos arbitrarios.

Adjuntar al rol de la instancia EC2 una política IAM que permita únicamente
`ses:SendEmail` sobre el ARN de la identidad verificada en `us-east-1`.
Ejemplo de recurso: `arn:aws:ses:us-east-1:<account-id>:identity/dominio.example`.
No configurar access keys permanentes: boto3 usa la cadena de credenciales de AWS,
incluido el rol de instancia. Limitar salida HTTPS a SES según la red de despliegue.

Despliegue seguro: verificar identidad, región, salida de red, rol y acceso de
producción de SES; cargar las variables en el entorno protegido de EC2; construir la
imagen con `boto3` incluido; desplegar sólo el backend tras aprobar el cambio. Mantener
el endpoint OTP detrás del HTTPS y los controles de acceso y tasa previstos antes de
publicarlo en Caddy. Validar primero con una cuenta adulta de prueba autorizada y
revisar métricas de SES sin registrar códigos ni contenido del mensaje. Si SES falla o
agota el timeout, la API responde 503 genérico y no confirma el desafío OTP.

Referencias: [identidades verificadas](https://docs.aws.amazon.com/ses/latest/dg/verify-addresses-and-domains.html),
[restricciones del sandbox](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html),
[permisos IAM](https://docs.aws.amazon.com/ses/latest/dg/control-user-access.html).
