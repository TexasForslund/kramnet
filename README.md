# Kramnet E-posttjänst

Backend-API för kramnet.se – en e-posttjänst som hanterar kunder, e-postkonton, betalningar via Swish och e-postkontohantering via Hostek.

## Stack

- **Python 3.12** + **FastAPI**
- **SQLAlchemy 2.0** (async) + **asyncpg**
- **Alembic** för databasmigrations
- **Pydantic Settings** för konfiguration
- **APScheduler** för schemalagda jobb (förnyelsepåminnelser etc.)
- **Postmark** för transaktionell e-post
- **Swish** för betalningar

## Komma igång

### 1. Skapa virtuell miljö och installera beroenden

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Konfigurera miljövariabler

```bash
cp .env.example .env
# Redigera .env med dina värden
```

### 3. Starta databasen (PostgreSQL)

```bash
# Skapa databasen
createdb kramnet

# Kör migreringar
alembic upgrade head
```

### 4. Starta servern

```bash
uvicorn app.main:app --reload
```

API-dokumentation finns på: http://localhost:8000/docs

## Projektstruktur

```
kramnet/
├── alembic/              # Databasmigreringar
├── app/
│   ├── api/routes/       # Endpoints per domän
│   ├── core/             # Config och databasanslutning
│   ├── models/           # SQLAlchemy ORM-modeller
│   ├── schemas/          # Pydantic request/response-schemas
│   ├── services/         # Affärslogik
│   ├── templates/        # Jinja2 HTML-templates
│   └── main.py           # App entry point
└── tests/
```

## API-endpoints

| Prefix | Beskrivning |
|--------|-------------|
| `/api/auth` | Inloggning och autentisering |
| `/api/customers` | Kundhantering |
| `/api/accounts` | E-postkonton (via Hostek) |
| `/api/payments` | Swish-betalningar |
| `/api/admin` | Adminpanel |
