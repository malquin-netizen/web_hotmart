import os
import sqlite3
import secrets
import smtplib

from email.message import EmailMessage
from datetime import datetime, timezone
from flask import (
    Flask, request, redirect, url_for,
    abort, render_template_string, jsonify
)

app = Flask(__name__)

# CONFIGURACIÓN
DB_PATH = os.getenv("DB_PATH", "ventas.db")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000")
CHECKOUT_URL = os.getenv("CHECKOUT_URL", "")
AUTOMATION_TOKEN = os.getenv("AUTOMATION_TOKEN", "")
HOTMART_WEBHOOK_TOKEN = os.getenv("HOTMART_WEBHOOK_TOKEN", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)


def fecha():
    return datetime.now(timezone.utc).isoformat()


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def iniciar_base():
    with conectar() as conn:

        conn.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            consentimiento INTEGER DEFAULT 0,
            token_baja TEXT NOT NULL,
            fecha_registro TEXT,
            fecha_baja TEXT,
            oferta_enviada TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaccion TEXT UNIQUE NOT NULL,
            email TEXT,
            producto TEXT,
            estado TEXT,
            importe REAL,
            moneda TEXT,
            fecha TEXT
        )
        """)


# ENVÍO DE CORREOS

def enviar_correo(destinatario, asunto, contenido):

    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM]):
        app.logger.warning("SMTP no configurado.")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = SMTP_FROM
    mensaje["To"] = destinatario
    mensaje.set_content(contenido)

    try:
        with smtplib.SMTP(
            SMTP_HOST, SMTP_PORT, timeout=20
        ) as servidor:

            servidor.starttls()
            servidor.login(SMTP_USER, SMTP_PASSWORD)
            servidor.send_message(mensaje)

        return True

    except Exception:
        app.logger.exception("Error enviando correo.")
        return False


# PÁGINA PRINCIPAL

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Método Éxito Digital</title>

<style>
body {
    font-family: Arial;
    max-width: 750px;
    margin: 50px auto;
    padding: 20px;
    color: #182230;
}

.container {
    padding: 30px;
    border: 1px solid #ddd;
    border-radius: 15px;
}

input[type=email] {
    width: 100%;
    padding: 12px;
    margin: 15px 0;
    box-sizing: border-box;
}

button {
    padding: 12px 20px;
    cursor: pointer;
}

</style>
</head>

<body>

<div class="container">

<h1>Método Éxito Digital</h1>

<p>
Descubrí nuestro ebook y conocé cómo aprovechar
sus contenidos.
</p>

{% if checkout %}
<p>
<a href="{{ checkout }}">COMPRAR EL EBOOK</a>
</p>
{% else %}
<p>Próximamente disponible.</p>
{% endif %}

<hr>

<h2>Recibir información</h2>

<form action="/clientes" method="POST">

<input
type="email"
name="email"
placeholder="Tu correo electrónico"
required
>

<label>
<input type="checkbox" name="consentimiento"
value="si" required>

Acepto recibir información y ofertas por correo.
Puedo darme de baja cuando quiera.

</label>

<br><br>

<button type="submit">
QUIERO RECIBIR INFORMACIÓN
</button>

</form>

</div>

</body>
</html>
"""


@app.route("/")
def inicio():

    return render_template_string(
        HTML,
        checkout=CHECKOUT_URL
    )


# REGISTRO DE CLIENTES

@app.route("/clientes", methods=["POST"])
def registrar_cliente():

    email = request.form.get("email", "").strip().lower()

    consentimiento = (
        request.form.get("consentimiento") == "si"
    )

    if not email or "@" not in email:
        abort(400)

    if not consentimiento:
        abort(400)

    token = secrets.token_urlsafe(24)

    with conectar() as conn:

        conn.execute("""
        INSERT INTO clientes
        (email, consentimiento, token_baja, fecha_registro)

        VALUES (?, 1, ?, ?)

        ON CONFLICT(email) DO UPDATE SET
        consentimiento=1,
        fecha_baja=NULL
        """, (email, token, fecha()))

        cliente = conn.execute("""
        SELECT token_baja, oferta_enviada
        FROM clientes
        WHERE email=?
        """, (email,)).fetchone()

    url_baja = (
        PUBLIC_BASE_URL.rstrip("/")
        + "/baja/"
        + cliente["token_baja"]
    )

    contenido = f"""
Gracias por interesarte en Método Éxito Digital.

Podés conocer y comprar nuestro ebook aquí:

{CHECKOUT_URL}

Si ya no querés recibir correos:
{url_baja}
"""

    if (
        not cliente["oferta_enviada"]
        and enviar_correo(
            email,
            "Método Éxito Digital - Información del ebook",
            contenido
        )
    ):

        with conectar() as conn:

            conn.execute("""
            UPDATE clientes
            SET oferta_enviada=?
            WHERE email=?
            """, (fecha(), email))

    return redirect("/gracias")


@app.route("/gracias")
def gracias():

    return """
    <h1>¡Gracias!</h1>
    <p>Tu solicitud fue registrada correctamente.</p>
    """


# BAJA DE CORREOS

@app.route("/baja/<token>")
def baja(token):

    with conectar() as conn:

        conn.execute("""
        UPDATE clientes
        SET consentimiento=0,
            fecha_baja=?
        WHERE token_baja=?
        """, (fecha(), token))

    return "Tu solicitud de baja fue registrada."


# WEBHOOK DE HOTMART

@app.route("/webhooks/hotmart", methods=["POST"])
def hotmart():

    recibido = request.headers.get(
        "X-HOTMART-HOTTOK", ""
    )

    if (
        not HOTMART_WEBHOOK_TOKEN
        or not secrets.compare_digest(
            recibido,
            HOTMART_WEBHOOK_TOKEN
        )
    ):
        abort(403)

    datos = request.get_json(silent=True) or {}

    compra = (datos.get("data") or {}).get("purchase") or {}
    comprador = (datos.get("data") or {}).get("buyer") or {}
    producto = (datos.get("data") or {}).get("product") or {}

    transaccion = (
        compra.get("transaction")
        or compra.get("order_id")
    )

    if not transaccion:
        return jsonify(error="Falta transacción"), 400

    estado = (
        compra.get("status")
        or datos.get("event")
        or "UNKNOWN"
    )

    precio = compra.get("price") or {}

    importe = precio.get("value")

    try:
        importe = float(importe) if importe is not None else None
    except (ValueError, TypeError):
        importe = None

    with conectar() as conn:

        conn.execute("""
        INSERT INTO ventas
        (transaccion, email, producto, estado,
         importe, moneda, fecha)

        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(transaccion) DO UPDATE SET
        email=excluded.email,
        producto=excluded.producto,
        estado=excluded.estado,
        importe=excluded.importe,
        moneda=excluded.moneda,
        fecha=excluded.fecha
        """, (
            str(transaccion),
            comprador.get("email"),
            producto.get("name"),
            str(estado),
            importe,
            precio.get("currency_code"),
            fecha()
        ))

    return jsonify(ok=True)


# PANEL DE ADMINISTRACIÓN

@app.route("/admin")
def panel():

    token = request.headers.get(
        "X-Automation-Token"
    ) or request.args.get("token", "")

    if (
        not AUTOMATION_TOKEN
        or not secrets.compare_digest(
            token,
            AUTOMATION_TOKEN
        )
    ):
        abort(403)

    with conectar() as conn:

        clientes = conn.execute("""
        SELECT COUNT(*) AS total
        FROM clientes
        WHERE consentimiento=1
        """).fetchone()["total"]

        ventas = conn.execute("""
        SELECT transaccion, email, producto,
               estado, importe, moneda
        FROM ventas
        ORDER BY id DESC
        LIMIT 30
        """).fetchall()

    resultado = ""

    for venta in ventas:

        resultado += f"""
        <tr>
        <td>{venta['transaccion']}</td>
        <td>{venta['email'] or ''}</td>
        <td>{venta['producto'] or ''}</td>
        <td>{venta['estado']}</td>
        <td>{venta['importe'] or ''} {venta['moneda'] or ''}</td>
        </tr>
        """

    return f"""
    <h1>Digital Nexora - Panel de ventas</h1>

    <h2>Clientes autorizados: {clientes}</h2>

    <table border="1" cellpadding="8">

    <tr>
    <th>Transacción</th>
    <th>Email</th>
    <th>Producto</th>
    <th>Estado</th>
    <th>Importe</th>
    </tr>

    {resultado}

    </table>
    """


# COMPROBAR FUNCIONAMIENTO

@app.route("/health")
def health():

    return jsonify(status="ok")


iniciar_base()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
    )
