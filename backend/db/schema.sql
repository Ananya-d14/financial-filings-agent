-- =============================================================================
-- Financial Filings Analyst — schema v1
-- Auto-applied on first Postgres container start via docker-entrypoint-initdb.d.
-- For migrations after v1, use Alembic in backend/db/migrations/.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- companies — the locked 20-ticker universe.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    cik             BIGINT PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    sector          TEXT,
    sic_code        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- filings — one row per (cik, accession_number).
-- content_sha256 enables idempotent re-ingestion.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cik                 BIGINT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
    accession_number    TEXT NOT NULL UNIQUE,
    form                TEXT NOT NULL,                  -- '10-K', '10-Q', '8-K'
    fiscal_year         INTEGER NOT NULL,
    fiscal_period       TEXT,                           -- 'FY', 'Q1', 'Q2', 'Q3'
    filed_date          DATE NOT NULL,
    period_end          DATE NOT NULL,
    primary_doc_url     TEXT,
    content_sha256      TEXT NOT NULL,
    raw_path            TEXT,                           -- on-disk cache of raw filing
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (form IN ('10-K', '10-Q', '8-K'))
);
CREATE INDEX IF NOT EXISTS idx_filings_cik_year ON filings (cik, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_filings_form ON filings (form);
CREATE INDEX IF NOT EXISTS idx_filings_period_end ON filings (period_end);

-- ---------------------------------------------------------------------------
-- filing_sections — section-aware structure preserved for citation offsets.
-- For 10-K: Item 1, 1A, 7, 7A, 8, etc. For 8-K: 1.01, 2.02, 5.02, etc.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filing_sections (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id               UUID NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    section                 TEXT NOT NULL,              -- e.g. 'Item 1A', 'MD&A'
    item_label              TEXT,                       -- raw label from source
    char_offset_start       INTEGER NOT NULL,
    char_offset_end         INTEGER NOT NULL,
    text_md                 TEXT NOT NULL,              -- markdown rendering
    raw_html                TEXT,                       -- optional raw fragment
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (char_offset_end >= char_offset_start)
);
CREATE INDEX IF NOT EXISTS idx_filing_sections_filing ON filing_sections (filing_id, section);

-- ---------------------------------------------------------------------------
-- xbrl_facts — structured GAAP-tagged facts. THE source of truth for numbers.
-- canonical_concept normalises tag heterogeneity (e.g. multiple revenue tags
-- → 'us-gaap:Revenues:canonical').
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xbrl_facts (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cik                     BIGINT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
    concept                 TEXT NOT NULL,              -- raw tag from XBRL
    canonical_concept       TEXT NOT NULL,              -- normalised concept
    period_start            DATE,
    period_end              DATE NOT NULL,
    value                   NUMERIC(28, 4) NOT NULL,
    unit                    TEXT NOT NULL,              -- 'USD', 'shares', etc.
    form                    TEXT NOT NULL,
    accession_number        TEXT NOT NULL,
    fiscal_year             INTEGER NOT NULL,
    fiscal_period           TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (form IN ('10-K', '10-Q', '8-K')),
    UNIQUE (cik, canonical_concept, period_start, period_end, form, accession_number)
);
CREATE INDEX IF NOT EXISTS idx_xbrl_canon ON xbrl_facts (cik, canonical_concept, period_end);
CREATE INDEX IF NOT EXISTS idx_xbrl_concept ON xbrl_facts (concept);
CREATE INDEX IF NOT EXISTS idx_xbrl_period_end ON xbrl_facts (period_end);

-- ---------------------------------------------------------------------------
-- chunks — narrative text chunks for hybrid retrieval.
-- text_tsv is the FTS column (BM25 via Postgres FTS).
-- qdrant_point_id is the 1:1 link to the dense vector in Qdrant.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id               UUID NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    section                 TEXT NOT NULL,
    item_label              TEXT,
    char_offset_start       INTEGER NOT NULL,
    char_offset_end         INTEGER NOT NULL,
    text                    TEXT NOT NULL,
    token_count             INTEGER,
    text_tsv                TSVECTOR,
    qdrant_point_id         UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (char_offset_end >= char_offset_start)
);
CREATE INDEX IF NOT EXISTS idx_chunks_filing ON chunks (filing_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks (filing_id, section);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN (text_tsv);

-- Auto-populate text_tsv on insert/update.
CREATE OR REPLACE FUNCTION chunks_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.text_tsv := to_tsvector('english', COALESCE(NEW.text, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chunks_tsv_update ON chunks;
CREATE TRIGGER chunks_tsv_update
    BEFORE INSERT OR UPDATE OF text ON chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_tsv_trigger();

-- ---------------------------------------------------------------------------
-- ingestion_runs — operational log for resumability + metrics.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at             TIMESTAMPTZ,
    tickers                 TEXT[],
    fiscal_years            INTEGER[],
    forms                   TEXT[],
    filings_added           INTEGER NOT NULL DEFAULT 0,
    filings_skipped         INTEGER NOT NULL DEFAULT 0,
    chunks_added            INTEGER NOT NULL DEFAULT 0,
    xbrl_facts_added        INTEGER NOT NULL DEFAULT 0,
    error_count             INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT
);

-- ---------------------------------------------------------------------------
-- Seed the locked 20-ticker universe.
-- CIKs verified against SEC EDGAR; will be re-checked at first ingestion run.
-- ---------------------------------------------------------------------------
INSERT INTO companies (cik, ticker, name, sector) VALUES
    (789019,    'MSFT',  'Microsoft Corporation',                   'Technology'),
    (320193,    'AAPL',  'Apple Inc.',                              'Technology'),
    (1652044,   'GOOGL', 'Alphabet Inc.',                           'Technology'),
    (1018724,   'AMZN',  'Amazon.com, Inc.',                        'Consumer Discretionary'),
    (1326801,   'META',  'Meta Platforms, Inc.',                    'Technology'),
    (1045810,   'NVDA',  'NVIDIA Corporation',                      'Technology'),
    (1318605,   'TSLA',  'Tesla, Inc.',                             'Consumer Discretionary'),
    (2488,      'AMD',   'Advanced Micro Devices, Inc.',            'Technology'),
    (50863,     'INTC',  'Intel Corporation',                       'Technology'),
    (1108524,   'CRM',   'Salesforce, Inc.',                        'Technology'),
    (1341439,   'ORCL',  'Oracle Corporation',                      'Technology'),
    (19617,     'JPM',   'JPMorgan Chase & Co.',                    'Financials'),
    (70858,     'BAC',   'Bank of America Corporation',             'Financials'),
    (104169,    'WMT',   'Walmart Inc.',                            'Consumer Staples'),
    (909832,    'COST',  'Costco Wholesale Corporation',            'Consumer Staples'),
    (200406,    'JNJ',   'Johnson & Johnson',                       'Health Care'),
    (78003,     'PFE',   'Pfizer Inc.',                             'Health Care'),
    (18230,     'CAT',   'Caterpillar Inc.',                        'Industrials'),
    (34088,     'XOM',   'Exxon Mobil Corporation',                 'Energy'),
    (59478,     'LLY',   'Eli Lilly and Company',                   'Health Care')
ON CONFLICT (cik) DO NOTHING;
