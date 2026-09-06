# Por que se mueve mi cartera

Aplicacion web ligera que cada dia mira tus acciones y ETFs, detecta los que se
han movido de forma relevante, busca noticias recientes sobre ellos y genera una
explicacion de 2-3 frases en lenguaje sencillo con un modelo de IA.

Pensada para vivir en una Raspberry Pi: FastAPI + SQLite + HTML/CSS/JS sin
frameworks. Sin dependencias de pago en ningun punto.

> **Esto no es asesoramiento financiero.** Los resumenes los genera un modelo de
> IA a partir de titulares publicos y pueden ser incompletos o erroneos.

---

## Que es gratis y por que

| Pieza | Fuente | Cuenta | Tarjeta | Limite real |
|---|---|---|---|---|
| Precios (principal) | Endpoint publico de graficos de Yahoo | No | No | Exige un User-Agent de escritorio (ver abajo); 1 peticion por ticker, espaciadas 1,5 s |
| Precios (alternativa) | `yfinance` (opcional) | No | No | Mismo backend de Yahoo, gestiona cookie+crumb |
| Precios (ultimo recurso) | Stooq CSV | No | No | **Hoy protegido con verificacion JavaScript: casi siempre falla** |
| Noticias por valor | Google News RSS | No | No | Sin limite publicado; 1 peticion por ticker movido |
| Noticias por valor | Yahoo Finance (via `yfinance`) | No | No | Solo si instalas el extra opcional |
| Contexto de mercado | RSS de CNBC, Yahoo Finance y Expansion | No | No | Feeds abiertos |
| IA | Groq / Gemini / OpenRouter / Ollama | Segun cual | **No** | Ver mas abajo |

Ninguna de estas piezas pide datos bancarios. Las que requieren cuenta se
registran con email o cuenta de Google, y su capa gratuita no es un periodo de
prueba: no caduca.

### Sobre los proveedores de IA

Se elige con `LLM_PROVIDER` y se puede cambiar sin tocar codigo.

- **`groq`** (por defecto, modelo `openai/gpt-oss-120b`). Clave gratuita en
  <https://console.groq.com/keys> con login de Google o email. Sin tarjeta.
  Limites amplios (del orden de decenas de peticiones por minuto y miles al dia).
  Es el mas rapido y el que menos carga deja en la Pi, porque el modelo corre en
  su infraestructura.

  Groq retira modelos cada pocos meses: los Llama 3.x ya no estan. Si un dia
  `GROQ_MODEL` empieza a dar error, mira cuales siguen vivos con
  `curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"`.
  Probados en esta cartera: `openai/gpt-oss-120b` da explicaciones concretas y
  bien matizadas; `openai/gpt-oss-20b` y `qwen/qwen3.8-27b` funcionan pero son
  mucho mas conservadores y responden "sin causa clara" incluso cuando los
  titulares si explican el movimiento.
- **`gemini`**. Clave gratuita en <https://aistudio.google.com/apikey> con cuenta
  de Google. Sin tarjeta. En la capa gratuita, Google puede usar tus prompts para
  mejorar sus productos: tenlo en cuenta.
- **`openrouter`**. Clave gratuita en <https://openrouter.ai/keys>. Los modelos
  con sufijo `:free` no cuestan nada, pero el cupo diario es pequeno si nunca has
  comprado creditos, y el catalogo de modelos gratuitos rota.
- **`ollama`**. Sin cuenta, sin clave, sin internet y con privacidad total. Ver
  la seccion de Raspberry Pi para saber si tu modelo de Pi da la talla.
- **`none`**. Desactiva la IA: la app sigue mostrando precios y titulares, y las
  explicaciones aparecen como "sin explicacion disponible".

Con una cartera de 10-30 valores y un umbral del 2-3 %, el job hace del orden de
3 a 8 llamadas al modelo por dia. Cualquiera de las capas gratuitas sobra.

Puedes definir `LLM_FALLBACK_PROVIDER` para que, si el primario falla, se intente
con otro (por ejemplo `groq` como primario y `ollama` como reserva).

---

## Instalacion

```bash
git clone <tu-repo> cartera && cd cartera
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Opcional: via alternativa de precios + noticias de Yahoo.
# Arrastra pandas y numpy (~150 MB). En una Pi tarda un rato.
.venv/bin/pip install -r requirements-optional.txt

cp .env.example .env
$EDITOR .env
```

Configura como minimo `TICKERS` y, si usas Groq, `GROQ_API_KEY`.

Los tickers usan el formato de Yahoo Finance: `AAPL`, `MSFT` para EE. UU.;
`SAN.MC` (Madrid), `VWCE.DE` (Xetra), `MC.PA` (Paris), `ISP.MI` (Milan),
`SHEL.L` (Londres) para Europa.

## Gestionar la cartera

La cartera se edita **desde la propia web**, en el desplegable "Mi cartera".
Escribe el nombre de la empresa o del fondo y aparecen los resultados con su
mercado; pulsa Anadir en el que corresponda. Tambien busca por ticker de Yahoo o
por **ISIN**, que es como suelen venir los valores en los extractos de broker
(`IE000BI8OT95` encuentra el ETF, aunque tu tengas apuntada otra cotizacion).
Los cambios son inmediatos y no hace falta reiniciar nada.

Cada resultado muestra **la variacion del ultimo dia**, con el mismo criterio y
el mismo numero que luego veras en la ficha del valor (ultimo cierre frente al
anterior).

Un mismo valor cotiza en varios mercados y **no todos tienen datos de precio en
Yahoo**: las cotizaciones secundarias (Stuttgart, Hamburgo, Munich...) suelen
aparecer en el buscador de Yahoo pero venir vacias. El buscador de la aplicacion
**las descarta**: si un valor no se puede seguir, no se ofrece. Por eso una
busqueda puede devolver menos resultados de los que daria Yahoo, o ninguno.

Ojo al implementarlo: Yahoo rellena `fulldayChangePercent` incluso para simbolos
sin serie de precios, asi que ese campo no sirve para decidir. El filtro exige al
menos dos cierres reales en `close`.

La comprobacion de precios de los resultados se hace con **una sola peticion**
para toda la lista (endpoint `spark`; el de cotizaciones `v7/quote` devuelve 401
desde 2024 porque pide autenticacion), y se cachea 2 minutos.

`TICKERS` del `.env` solo es la **semilla**: se usa para rellenar la cartera la
primera vez que arranca la aplicacion. A partir de ahi manda la base de datos, y
tocar el `.env` ya no cambia nada.

Al anadir un valor se comprueba antes que existe en Yahoo Finance y, si existe,
se descarga su precio al momento sin esperar al job nocturno. Si en ese instante
la fuente de precios no responde, el valor se anade igualmente con un aviso: un
429 pasajero no debe impedirte configurar tu cartera.

**Renombrar.** Cada valor de la lista tiene un boton Renombrar: le pones el
nombre que tu uses ("Mi MSCI World", "Plata") y es el que se ve en toda la
aplicacion. Para volver al nombre oficial, guarda el campo vacio. Precedencia:
nombre puesto a mano > `TICKER_NAMES` del `.env` > nombre que devuelve Yahoo.

**La lista principal solo muestra los valores que tienes ahora en la cartera.**
Quitar un valor no borra su historico de la base de datos (si lo vuelves a
anadir, reaparece con sus dias antiguos), pero deja de verse.

Tambien por API:

```bash
curl localhost:8000/api/tickers
curl "localhost:8000/api/search?q=msci+world"
curl -X POST localhost:8000/api/tickers -H "Content-Type: application/json" -d '{"ticker":"AMZN"}'
curl -X PATCH localhost:8000/api/tickers/AMZN -H "Content-Type: application/json" -d '{"name":"Mi Amazon"}'
curl -X DELETE localhost:8000/api/tickers/AMZN
```

Si has puesto `REFRESH_TOKEN`, anade la cabecera `-H "X-Refresh-Token: ..."` a
las tres ultimas.

## Uso

```bash
# Lanzar el job una vez (util para probar)
.venv/bin/python scripts/run_job.py -v

# Solo unos tickers concretos
.venv/bin/python scripts/run_job.py --tickers AAPL,SAN.MC

# Arrancar la web
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abre <http://localhost:8000>. El boton "Actualizar ahora" lanza el job a mano.

Endpoints: `GET /api/portfolio?day=YYYY-MM-DD`, `GET /api/days`,
`GET /api/health`, `POST /api/refresh`, `GET /api/search?q=`,
`GET|POST /api/tickers`, `PATCH|DELETE /api/tickers/{ticker}`.

Si expones la app fuera de tu red local, pon un `REFRESH_TOKEN` en el `.env`;
entonces `POST /api/refresh` y los endpoints que modifican la cartera exigen la
cabecera `X-Refresh-Token`.

## Noticias de fondos y ETFs

Un ETF no tiene noticias propias. Buscar "WisdomTree Physical Silver" no devuelve
nada util: lo que mueve a ese producto es **el precio de la plata**, y de eso si
hay titulares. Por eso `app/sources/topics.py` traduce el nombre del fondo al
tema que hay que buscar, y el prompt le dice al modelo que los titulares van del
subyacente y no de una empresa.

Cubre metales, petroleo, bitcoin, los grandes indices (S&P 500, Nasdaq, MSCI
World, emergentes, Stoxx Europe 600, Ibex, DAX...), renta fija y algunos
sectores. Una accion normal no encaja en ninguna regla y se sigue buscando por su
propio nombre: "Barrick Gold Corporation" **no** se confunde con el oro, porque
las reglas exigen cadenas como "physical gold".

El efecto es grande. Con una cartera de ETFs, antes casi todo salia "sin causa
clara"; ahora salen explicaciones reales:

| Valor | Antes | Ahora |
|---|---|---|
| SGBS.MI (oro) | Sin causa clara | "El precio del oro ha caido... por el repunte de la rupia" |
| EIMI.MI (emergentes) | Sin causa clara | "Los inversores reducen expectativas de subidas de tipos de la Fed" |

Si algun fondo no se detecta bien, en "Mi cartera" cada valor tiene un boton
**Tema** para escribir a mano lo que hay que buscar. Se guarda por valor y manda
sobre la deteccion automatica; dejalo vacio para volver a ella.

## Como decide que explicar

1. Descarga el cierre de hoy y el anterior de cada ticker.
2. Marca como relevante todo movimiento con `|variacion| >= MOVE_THRESHOLD_PCT`
   (2,5 % por defecto).
3. Solo para esos, busca titulares de las ultimas `NEWS_MAX_AGE_HOURS` (48 h).
4. Si es un fondo, busca el tema que lo mueve en vez de su nombre (ver arriba).
5. Manda al modelo el movimiento y los titulares, con instrucciones explicitas de
   no inventar y de decir que **no hay causa clara** si los titulares no explican
   nada. Esa respuesta se marca aparte en la interfaz.

## Degradacion ante fallos

Ningun fallo de una fuente rompe la aplicacion:

| Fallo | Comportamiento |
|---|---|
| Yahoo falla o devuelve 429 | Prueba el segundo host de Yahoo, luego `yfinance` y luego Stooq; si aun asi no hay ningun precio, el job se reprograma solo 45 min despues (hasta 2 veces) |
| Ninguna fuente de precio responde | Guarda el hueco y muestra "sin datos de precio"; **no** machaca los datos buenos que ya hubiera de hoy |
| No hay noticias del valor | Usa titulares generales de mercado como contexto |
| No hay ninguna noticia | Ni siquiera llama al modelo: muestra el mensaje de "movimiento general del mercado" |
| El modelo devuelve una respuesta cortada | Se descarta: media frase es peor que nada. Los modelos con razonamiento gastan tokens pensando, por eso `MAX_TOKENS` es 1200 y no 300 |
| El modelo de IA falla o esta caido | Muestra "sin explicacion disponible" y deja visibles los titulares; si ya habia una explicacion buena de hoy, la conserva |
| Un ticker falla | El resto del job continua |

## Despliegue en Raspberry Pi

Sin Docker: en ARM anade capas de complicacion (compilar ruedas, mas RAM, mas
escrituras en la SD) sin aportar nada aqui. `systemd` es mas simple y arranca
solo al encender la Pi.

```bash
# 1. Codigo y dependencias en /home/isaac/cartera
sudo apt update && sudo apt install -y python3-venv python3-pip
git clone <tu-repo> /home/isaac/cartera && cd /home/isaac/cartera
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env

# 2. Comprobar que el job funciona antes de automatizar nada
.venv/bin/python scripts/run_job.py -v

# 3. Servicio web
sudo cp deploy/cartera-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cartera-web
systemctl status cartera-web
```

Las unidades de `deploy/` apuntan a `isaac` y `/home/isaac/cartera`. Si tu
usuario o tu ruta son otros, editalas antes de copiarlas.

La app queda en `http://<ip-de-la-pi>:8000`.

### Programacion del job

Hay dos opciones; **usa solo una**.

**A. Scheduler interno (por defecto).** Con `SCHEDULER_ENABLED=true`, el propio
proceso web lanza el job de lunes a viernes a la hora de `JOB_HOUR`/`JOB_MINUTE`.
Nada mas que hacer. Importante: arranca uvicorn con `--workers 1` (como hace el
`.service`), o tendras un scheduler por worker.

**B. Timer de systemd.** Mas robusto: el job corre en su propio proceso y no
consume RAM entre ejecuciones. Pon `SCHEDULER_ENABLED=false` en el `.env` y:

```bash
sudo cp deploy/cartera-job.service deploy/cartera-job.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cartera-job.timer
systemctl list-timers cartera-job.timer
journalctl -u cartera-job -n 50    # ver el ultimo resultado
```

### Consumo

El servicio web en reposo ronda los 60-90 MB de RAM y practicamente 0 % de CPU.
El job dura entre unos segundos y un par de minutos segun cuantos tickers se
hayan movido. La base de datos crece del orden de unos pocos MB al ano.

Si instalas el extra opcional (`yfinance`, con pandas y numpy), el proceso pasa a
ocupar unos 150-200 MB. En una Pi de 4 GB no es problema, pero si vas justo,
quedate con la instalacion minima.

### Ollama en la Pi

Con una Raspberry Pi 4 **no lo recomiendo** como opcion principal: un modelo de
1B cuantizado genera a ~2 tokens/s y tiene la CPU al 100 % durante minutos por
cada explicacion, y los modelos de 3B se van a bastante mas. Sirve para probar,
no para el uso diario.

Para que sea comodo hace falta una **Pi 5 con 8 GB**: ahi `llama3.2:3b` o
`qwen2.5:3b` en Q4 ocupan ~2,5 GB de RAM y dan ~3-5 tokens/s, es decir unos
20-30 s por explicacion. Para 8 valores movidos son unos 4 minutos de job
nocturno, perfectamente asumible.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
# en .env:
#   LLM_PROVIDER=ollama
#   OLLAMA_MODEL=llama3.2:3b
```

Combinacion recomendada en Pi 4: `LLM_PROVIDER=groq` y, si quieres red de
seguridad sin depender de internet, `LLM_FALLBACK_PROVIDER=ollama` con un modelo
de 1B.

## Limitaciones que conviene conocer

- **Yahoo filtra por User-Agent, y esto es una trampa en una Raspberry Pi.**
  Devuelve 429 de forma sistematica a los User-Agent de Linux ARM
  (`X11; Linux aarch64`), por muy espaciadas que vayan las peticiones: no es un
  limite de frecuencia, es un filtro por cliente. Con un User-Agent de escritorio
  responde 200 sin problema. El codigo ya usa uno de escritorio en
  `app/sources/prices.py` y `app/sources/news.py`; **no lo cambies a uno de ARM
  aunque la app corra en una Pi**, porque dejaras la app sin precios.
- **`yfinance.Ticker().info` es cara y fragil.** Necesita cookie y crumb y falla
  con facilidad. El codigo no la usa: el nombre y la divisa ya vienen gratis en
  la respuesta del endpoint de graficos.
- **Stooq ya no sirve de fallback fiable.** Desde 2026 su descarga de CSV exige
  resolver una verificacion JavaScript. El codigo lo detecta y falla limpiamente,
  pero no cuentes con el.
- **La calidad de la explicacion depende de los titulares.** Muchas veces lo mas
  reciente de un valor es ruido del tipo "3 razones para comprar X". Por eso el
  prompt obliga al modelo a admitir cuando no hay causa clara, en lugar de
  fabricar una.
- **Precio con retraso.** Los datos de Yahoo llegan con unos 15 minutos de
  retraso. Es un resumen diario, no una herramienta de trading.
- **Fin de semana y festivos.** El ultimo cierre disponible puede ser de dias
  atras; la interfaz lo indica con una etiqueta "cierre del ...".

## Estructura

```
app/
  config.py         Variables de entorno (no hay ninguna clave en el codigo)
  db.py             SQLite: cartera (con alias), snapshots, noticias, explicaciones
  jobs.py           Orquestacion del job diario
  scheduler.py      APScheduler interno (alternativa al timer de systemd)
  main.py           FastAPI: pagina y endpoints JSON
  sources/
    prices.py       Yahoo chart -> yfinance -> Stooq
    news.py         Yahoo/yfinance -> Google News RSS -> contexto de mercado
  llm/
    base.py         Prompt y contrato comun
    providers.py    Groq, Ollama, Gemini, OpenRouter
    __init__.py     explain_move() con fallback y degradacion
  templates/, static/
scripts/run_job.py  Ejecuta el job una vez
deploy/             Unidades de systemd
```
