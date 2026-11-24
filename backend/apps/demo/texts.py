"""Spanish texts for the demo tickets and comments.

Titles and descriptions per category; replies shared by every category. Plain
data, picked at random by plan.py.
"""

# Category name -> [(title, description)].
TICKETS = {
    "Accesos y contraseñas": [
        (
            "No puedo iniciar sesión en el ERP",
            "Desde esta mañana el ERP me dice que la contraseña no es válida, "
            "aunque es la de siempre.",
        ),
        (
            "Cuenta bloqueada tras varios intentos",
            "He escrito mal la contraseña varias veces y ahora la cuenta aparece bloqueada.",
        ),
        (
            "Alta de usuario para una nueva incorporación",
            "El lunes empieza una persona nueva en administración. Necesita usuario de "
            "correo y acceso al ERP.",
        ),
        (
            "Baja de acceso de un empleado",
            "Un compañero ha dejado la empresa hoy. Hay que desactivar sus accesos.",
        ),
        (
            "No me llega el código de verificación",
            "Al entrar desde casa me pide un código por SMS, pero no llega.",
        ),
    ],
    "Correo electrónico": [
        (
            "No recibo correos externos",
            "Los correos de clientes no llegan desde ayer. Los internos sí funcionan.",
        ),
        (
            "Buzón lleno",
            "Me avisa de que el buzón está al 98 % y no puedo enviar adjuntos.",
        ),
        (
            "Correos en la carpeta de spam",
            "Los pedidos de un proveedor habitual llegan siempre a correo no deseado.",
        ),
        (
            "Firma corporativa desconfigurada",
            "Tras la última actualización la firma sale sin el logotipo.",
        ),
        (
            "No sincroniza el correo en el móvil",
            "En el móvil de empresa el correo dejó de actualizarse hace dos días.",
        ),
    ],
    "Red y VPN": [
        (
            "La VPN se desconecta cada pocos minutos",
            "Trabajando desde casa, la VPN se cae cada diez minutos más o menos.",
        ),
        (
            "Sin conexión en la sala de reuniones",
            "El punto de red de la sala grande no da conexión y el wifi va muy lento.",
        ),
        (
            "Wifi de invitados no funciona",
            "Las visitas no consiguen conectarse a la red de invitados.",
        ),
        (
            "Lentitud general de la red",
            "Desde esta mañana todo va muy lento, sobre todo las carpetas compartidas.",
        ),
    ],
    "Equipos y periféricos": [
        (
            "La impresora de planta no imprime",
            "Los trabajos se quedan en cola y la impresora no hace nada.",
        ),
        (
            "Portátil muy lento al arrancar",
            "Tarda más de diez minutos en estar operativo cada mañana.",
        ),
        (
            "Pantalla parpadea",
            "El monitor externo parpadea y a veces se queda en negro.",
        ),
        (
            "Solicitud de un segundo monitor",
            "Para el puesto de facturación necesitaríamos un segundo monitor.",
        ),
        (
            "El escáner no envía a la carpeta",
            "Al escanear da error al guardar en la carpeta compartida.",
        ),
    ],
    "Aplicaciones": [
        (
            "Error al generar informes en el ERP",
            "Al exportar el informe mensual aparece un error y no se genera el fichero.",
        ),
        (
            "Instalar programa de diseño",
            "Necesito una licencia e instalación del programa de diseño para el catálogo.",
        ),
        (
            "La aplicación de almacén se cierra sola",
            "Al escanear albaranes la aplicación se cierra sin mensaje.",
        ),
        (
            "Actualización pendiente del antivirus",
            "El antivirus avisa de que lleva semanas sin actualizarse.",
        ),
    ],
    "Facturación": [
        (
            "Factura del mes con importe incorrecto",
            "La factura de este mes incluye un servicio que dimos de baja.",
        ),
        (
            "Solicitud de duplicado de factura",
            "Necesitamos un duplicado de la factura del trimestre pasado para auditoría.",
        ),
        (
            "Cambio de datos fiscales",
            "Hemos cambiado de domicilio fiscal. Os envío los datos nuevos.",
        ),
    ],
    "Telefonía fija": [
        (
            "La centralita no desvía llamadas",
            "Las llamadas a recepción no se desvían a la extensión de guardia.",
        ),
        (
            "Teléfono sin línea",
            "El teléfono del despacho no tiene tono desde esta mañana.",
        ),
    ],
}

FIRST_RESPONSES = [
    "Hola, ya estamos con ello. Te escribimos en cuanto tengamos novedades.",
    "Gracias por avisar. Lo revisamos ahora mismo.",
    "Recibido. Estamos revisando la configuración y te contamos.",
    "Hola, lo tenemos. Vamos a hacer unas comprobaciones.",
]

INFO_REQUESTS = [
    "¿Nos puedes enviar una captura del error?",
    "¿Te ocurre también desde otro equipo?",
    "¿Puedes indicarnos a qué hora empezó a fallar?",
    "Para seguir necesitamos que reinicies el equipo y nos digas si persiste.",
]

CLIENT_REPLIES = [
    "Adjunto la captura.",
    "Sí, me pasa también desde otro equipo.",
    "Empezó a las 9 más o menos.",
    "Reiniciado, y sigue igual.",
]

INTERNAL_NOTES = [
    "Parece relacionado con el cambio de ayer en el servidor.",
    "Escalado al proveedor; esperamos respuesta.",
    "Revisado el registro: hay errores de autenticación repetidos.",
    "Mismo caso que otra incidencia de este cliente la semana pasada.",
]

SOLUTIONS = [
    "Solucionado: hemos corregido la configuración. ¿Nos confirmas que funciona?",
    "Hecho. Ya debería funcionar correctamente.",
    "Hemos aplicado el cambio. Si vuelve a pasar, reabre la incidencia.",
    "Resuelto tras reiniciar el servicio. Cualquier cosa, nos dices.",
]

REOPEN_REASONS = [
    "Ha vuelto a pasar esta mañana.",
    "Sigue sin funcionar, lo siento.",
    "Funcionó un rato, pero vuelve a fallar.",
]

THANKS = [
    "Confirmado, ya funciona. ¡Gracias!",
    "Todo bien, gracias por la rapidez.",
    "Perfecto, gracias.",
]
