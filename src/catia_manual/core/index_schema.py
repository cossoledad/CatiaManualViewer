"""Schema 2 additions. Each statement can run within a single upgrade transaction."""

SCHEMA_VERSION = 2
CHUNK_VERSION = "source-blocks-1"
STATEMENTS = (
    (
        "CREATE TABLE IF NOT EXISTS library_files(path TEXT PRIMARY KEY,size INTEGER "
        "NOT NULL,content_hash TEXT NOT NULL,kind TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,document_rowid "
        "INTEGER NOT NULL,block_id TEXT NOT NULL,fragment TEXT NOT NULL,heading_path "
        "TEXT NOT NULL,title TEXT NOT NULL,heading TEXT NOT NULL,keywords TEXT NOT "
        "NULL,body TEXT NOT NULL)"
    ),
    "CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_rowid)",
    (
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING "
        "fts5(title,heading,keywords,body,content=chunks,content_rowid=id,tokenize='porter"
        " unicode61',prefix='2 3 4')"
    ),
    ("CREATE VIRTUAL TABLE IF NOT EXISTS keyword_vocabulary USING fts5vocab(chunks_fts,'row')"),
    (
        "CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN INSERT "
        "INTO chunks_fts(rowid,title,heading,keywords,body) "
        "VALUES(new.id,new.title,new.heading,new.keywords,new.body); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN INSERT "
        "INTO chunks_fts(chunks_fts,rowid,title,heading,keywords,body) "
        "VALUES('delete',old.id,old.title,old.heading,old.keywords,old.body); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS documents_chunks_ad AFTER DELETE ON documents "
        "BEGIN DELETE FROM chunks WHERE document_rowid=old.id; END"
    ),
    (
        "CREATE TABLE IF NOT EXISTS term_aliases(alias TEXT NOT NULL,english TEXT NOT "
        "NULL,module TEXT NOT NULL DEFAULT '',PRIMARY KEY(alias,module))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS reader_locations(document_id TEXT PRIMARY "
        "KEY,source TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    ),
)
