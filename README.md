# CuentaConmigo

Este es un proyecto personal que desarrollé como experimento para aprender a consolidar y categorizar gastos bancarios leyendo notificaciones de Gmail.

## Qué hace

- Procesa exclusivamente correos de notificaciones de compra de BCI y Banco Chile/Edwards.
- Ignora cualquier otro correo que no provenga de esos remitentes.
- Extrae monto, fecha y comercio para mostrarlos en un dashboard privado.

## Por qué publico este código

Lo comparto por transparencia, ya que algunas personas me preguntaron cómo se asegura que no se procesen otros correos.  
Todo el filtrado se encuentra en el módulo `services/email_sync.py`.
