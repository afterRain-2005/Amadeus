use std::{
    collections::HashSet,
    env, fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const DB_FILE: &str = "amadeus.db";
const DEFAULT_TITLE: &str = "新对话";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationSummary {
    pub id: String,
    pub title: String,
    pub preview: String,
    pub updated_at: i64,
    pub message_count: u32,
    pub is_active: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationMessage {
    pub id: i64,
    pub role: String,
    pub content: String,
    pub created_at: i64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationSnapshot {
    pub active_id: String,
    pub conversations: Vec<ConversationSummary>,
    pub messages: Vec<ConversationMessage>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MemoryItem {
    pub id: i64,
    pub kind: String,
    pub content: String,
    pub source: String,
    pub weight: f64,
    pub updated_at: i64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateMemory {
    pub id: i64,
    pub content: String,
    pub weight: f64,
}

#[derive(Clone, Debug)]
pub struct StoredMessage {
    pub role: String,
    pub content: String,
}

#[derive(Clone)]
pub struct ConversationStore {
    path: PathBuf,
    active_id: Arc<Mutex<String>>,
}

impl ConversationStore {
    pub fn new(config_dir: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&config_dir)
            .map_err(|error| format!("创建会话数据目录失败：{error}"))?;
        let path = config_dir.join(DB_FILE);
        let mut connection = open(&path)?;
        init_schema(&connection)?;
        migrate_legacy(&mut connection, &legacy_data_candidates(&config_dir))?;
        let active_id = ensure_active_session(&connection)?;
        Ok(Self {
            path,
            active_id: Arc::new(Mutex::new(active_id)),
        })
    }

    pub fn active_id(&self) -> Result<String, String> {
        self.active_id
            .lock()
            .map(|value| value.clone())
            .map_err(|_| "会话状态锁已损坏".to_owned())
    }

    pub fn history_snapshot(&self, limit: usize) -> Result<Vec<StoredMessage>, String> {
        let active_id = self.active_id()?;
        let connection = open(&self.path)?;
        let mut statement = connection
            .prepare(
                "SELECT role, content FROM (
                    SELECT id, role, content FROM messages
                    WHERE session_id=?1 ORDER BY id DESC LIMIT ?2
                 ) ORDER BY id ASC",
            )
            .map_err(db_error)?;
        let rows = statement
            .query_map(params![active_id, limit as i64], |row| {
                Ok(StoredMessage {
                    role: row.get(0)?,
                    content: row.get(1)?,
                })
            })
            .map_err(db_error)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
    }

    pub fn record_turn(&self, user: &str, assistant: &str, source: &str) -> Result<(), String> {
        let active_id = self.active_id()?;
        let mut connection = open(&self.path)?;
        let transaction = connection.transaction().map_err(db_error)?;
        let now = now_ms();
        transaction
            .execute(
                "INSERT INTO messages(session_id, role, content, created_at)
                 VALUES (?1, 'user', ?2, ?3), (?1, 'assistant', ?4, ?3)",
                params![active_id, user, now, assistant],
            )
            .map_err(db_error)?;
        let current_title: String = transaction
            .query_row(
                "SELECT title FROM sessions WHERE id=?1",
                params![active_id],
                |row| row.get(0),
            )
            .map_err(db_error)?;
        let title = if current_title == DEFAULT_TITLE {
            title_from(user)
        } else {
            current_title
        };
        transaction
            .execute(
                "UPDATE sessions SET title=?2, updated_at=?3 WHERE id=?1",
                params![active_id, title, now],
            )
            .map_err(db_error)?;
        remember_turn(&transaction, user, assistant, source, now)?;
        transaction.commit().map_err(db_error)
    }

    pub fn memory_context(&self, query: &str, limit: usize) -> Result<Vec<MemoryItem>, String> {
        let connection = open(&self.path)?;
        let mut memories = read_memories(&connection, 400)?;
        let terms = query_terms(query);
        memories.sort_by(|left, right| {
            memory_score(right, &terms)
                .total_cmp(&memory_score(left, &terms))
                .then_with(|| right.updated_at.cmp(&left.updated_at))
        });
        memories.truncate(limit);
        Ok(memories)
    }

    pub fn snapshot(&self) -> Result<ConversationSnapshot, String> {
        let active_id = self.active_id()?;
        let connection = open(&self.path)?;
        let mut statement = connection
            .prepare(
                "SELECT s.id, s.title, s.updated_at, COUNT(m.id),
                        COALESCE((SELECT content FROM messages p
                                  WHERE p.session_id=s.id ORDER BY p.id DESC LIMIT 1), '')
                 FROM sessions s LEFT JOIN messages m ON m.session_id=s.id
                 GROUP BY s.id ORDER BY s.updated_at DESC",
            )
            .map_err(db_error)?;
        let conversations = statement
            .query_map([], |row| {
                let id: String = row.get(0)?;
                Ok(ConversationSummary {
                    is_active: id == active_id,
                    id,
                    title: row.get(1)?,
                    updated_at: row.get(2)?,
                    message_count: row.get::<_, i64>(3)?.try_into().unwrap_or(u32::MAX),
                    preview: truncate(&row.get::<_, String>(4)?, 54),
                })
            })
            .map_err(db_error)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(db_error)?;
        let mut message_statement = connection
            .prepare(
                "SELECT id, role, content, created_at FROM messages
                 WHERE session_id=?1 ORDER BY id ASC",
            )
            .map_err(db_error)?;
        let messages = message_statement
            .query_map(params![active_id], |row| {
                Ok(ConversationMessage {
                    id: row.get(0)?,
                    role: row.get(1)?,
                    content: row.get(2)?,
                    created_at: row.get(3)?,
                })
            })
            .map_err(db_error)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(db_error)?;
        Ok(ConversationSnapshot {
            active_id,
            conversations,
            messages,
        })
    }

    pub fn create_session(&self) -> Result<ConversationSnapshot, String> {
        let id = Uuid::new_v4().simple().to_string();
        let now = now_ms();
        let connection = open(&self.path)?;
        connection
            .execute(
                "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?1, ?2, ?3, ?3)",
                params![id, DEFAULT_TITLE, now],
            )
            .map_err(db_error)?;
        self.set_active(id)?;
        self.snapshot()
    }

    pub fn switch_session(&self, id: &str) -> Result<ConversationSnapshot, String> {
        validate_id(id)?;
        let connection = open(&self.path)?;
        let exists = connection
            .query_row(
                "SELECT 1 FROM sessions WHERE id=?1",
                params![id],
                |_| Ok(()),
            )
            .optional()
            .map_err(db_error)?
            .is_some();
        if !exists {
            return Err("要切换的会话不存在".to_owned());
        }
        self.set_active(id.to_owned())?;
        self.snapshot()
    }

    pub fn delete_session(&self, id: &str) -> Result<ConversationSnapshot, String> {
        validate_id(id)?;
        let mut connection = open(&self.path)?;
        let transaction = connection.transaction().map_err(db_error)?;
        let count: i64 = transaction
            .query_row("SELECT COUNT(*) FROM sessions", [], |row| row.get(0))
            .map_err(db_error)?;
        if count <= 1 {
            return Err("至少保留一个会话；可以使用“清空当前会话”".to_owned());
        }
        let changed = transaction
            .execute("DELETE FROM sessions WHERE id=?1", params![id])
            .map_err(db_error)?;
        if changed == 0 {
            return Err("要删除的会话不存在".to_owned());
        }
        let next_id: String = transaction
            .query_row(
                "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1",
                [],
                |row| row.get(0),
            )
            .map_err(db_error)?;
        transaction.commit().map_err(db_error)?;
        if self.active_id()? == id {
            self.set_active(next_id)?;
        }
        self.snapshot()
    }

    pub fn clear_active(&self) -> Result<(), String> {
        let id = self.active_id()?;
        let connection = open(&self.path)?;
        connection
            .execute(
                "UPDATE sessions SET title=?2, updated_at=?3 WHERE id=?1",
                params![id, DEFAULT_TITLE, now_ms()],
            )
            .map_err(db_error)?;
        connection
            .execute("DELETE FROM messages WHERE session_id=?1", params![id])
            .map_err(db_error)?;
        Ok(())
    }

    pub fn list_memories(&self) -> Result<Vec<MemoryItem>, String> {
        read_memories(&open(&self.path)?, 500)
    }

    pub fn update_memory(&self, input: UpdateMemory) -> Result<Vec<MemoryItem>, String> {
        let content = normalize(&input.content);
        if input.id <= 0 || content.is_empty() || content.chars().count() > 500 {
            return Err("记忆内容必须是 1–500 个字符".to_owned());
        }
        if !input.weight.is_finite() || !(0.1..=3.0).contains(&input.weight) {
            return Err("记忆权重必须在 0.1–3.0 之间".to_owned());
        }
        let connection = open(&self.path)?;
        let changed = connection
            .execute(
                "UPDATE memories SET content=?2, weight=?3, updated_at=?4 WHERE id=?1",
                params![input.id, content, input.weight, now_ms()],
            )
            .map_err(|error| {
                if error.to_string().contains("UNIQUE") {
                    "相同记忆已经存在".to_owned()
                } else {
                    db_error(error)
                }
            })?;
        if changed == 0 {
            return Err("要修改的记忆不存在".to_owned());
        }
        self.list_memories()
    }

    pub fn delete_memory(&self, id: i64) -> Result<Vec<MemoryItem>, String> {
        if id <= 0 {
            return Err("记忆 ID 无效".to_owned());
        }
        open(&self.path)?
            .execute("DELETE FROM memories WHERE id=?1", params![id])
            .map_err(db_error)?;
        self.list_memories()
    }

    pub fn clear_memories(&self) -> Result<(), String> {
        open(&self.path)?
            .execute("DELETE FROM memories", [])
            .map_err(db_error)?;
        Ok(())
    }

    fn set_active(&self, id: String) -> Result<(), String> {
        let connection = open(&self.path)?;
        connection
            .execute(
                "INSERT INTO app_state(key, value) VALUES ('active_session', ?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![id],
            )
            .map_err(db_error)?;
        *self
            .active_id
            .lock()
            .map_err(|_| "会话状态锁已损坏".to_owned())? = id;
        Ok(())
    }
}

fn open(path: &Path) -> Result<Connection, String> {
    let connection = Connection::open(path).map_err(db_error)?;
    connection
        .busy_timeout(std::time::Duration::from_secs(3))
        .map_err(db_error)?;
    connection
        .execute_batch("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL;")
        .map_err(db_error)?;
    Ok(connection)
}

fn init_schema(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS messages_session_id ON messages(session_id, id);
             CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(kind, content)
             );
             CREATE INDEX IF NOT EXISTS memories_weight ON memories(weight DESC, updated_at DESC);
             CREATE TABLE IF NOT EXISTS app_state(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS legacy_imports(
                source TEXT PRIMARY KEY,
                imported_at INTEGER NOT NULL
             );",
        )
        .map_err(db_error)
}

fn ensure_active_session(connection: &Connection) -> Result<String, String> {
    if let Some(id) = connection
        .query_row(
            "SELECT value FROM app_state WHERE key='active_session'
             AND EXISTS(SELECT 1 FROM sessions WHERE id=value)",
            [],
            |row| row.get(0),
        )
        .optional()
        .map_err(db_error)?
    {
        return Ok(id);
    }
    let id = connection
        .query_row(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(db_error)?
        .unwrap_or_else(|| Uuid::new_v4().simple().to_string());
    connection
        .execute(
            "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?3)",
            params![id, DEFAULT_TITLE, now_ms()],
        )
        .map_err(db_error)?;
    connection
        .execute(
            "INSERT INTO app_state(key, value) VALUES ('active_session', ?1)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![id],
        )
        .map_err(db_error)?;
    Ok(id)
}

fn remember_turn(
    connection: &Connection,
    user: &str,
    assistant: &str,
    source: &str,
    now: i64,
) -> Result<(), String> {
    for fact in extract_facts(user) {
        upsert_memory(connection, "fact", &fact, source, 1.4, now)?;
    }
    if user.chars().count() >= 8 {
        let episode = format!(
            "用户说：{} / 红莉栖答：{}",
            truncate(user, 140),
            truncate(assistant, 120)
        );
        upsert_memory(connection, "episode", &episode, source, 0.6, now)?;
    }
    Ok(())
}

fn upsert_memory(
    connection: &Connection,
    kind: &str,
    content: &str,
    source: &str,
    weight: f64,
    now: i64,
) -> Result<(), String> {
    connection
        .execute(
            "INSERT INTO memories(kind, content, source, weight, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?5)
             ON CONFLICT(kind, content) DO UPDATE SET
                source=excluded.source,
                weight=MIN(3.0, MAX(memories.weight, excluded.weight) + 0.05),
                updated_at=excluded.updated_at",
            params![kind, normalize(content), source, weight, now],
        )
        .map_err(db_error)?;
    Ok(())
}

fn read_memories(connection: &Connection, limit: usize) -> Result<Vec<MemoryItem>, String> {
    let mut statement = connection
        .prepare(
            "SELECT id, kind, content, source, weight, updated_at FROM memories
             ORDER BY weight DESC, updated_at DESC LIMIT ?1",
        )
        .map_err(db_error)?;
    statement
        .query_map(params![limit as i64], |row| {
            Ok(MemoryItem {
                id: row.get(0)?,
                kind: row.get(1)?,
                content: row.get(2)?,
                source: row.get(3)?,
                weight: row.get(4)?,
                updated_at: row.get(5)?,
            })
        })
        .map_err(db_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(db_error)
}

fn extract_facts(text: &str) -> Vec<String> {
    let normalized = normalize(text);
    let mut facts = Vec::new();
    for marker in [
        "记住",
        "我叫",
        "我的名字是",
        "叫我",
        "我喜欢",
        "我爱",
        "我讨厌",
        "我不喜欢",
        "我是",
        "我正在",
        "I am",
        "I'm",
        "my name is",
        "I like",
        "I love",
        "I hate",
        "remember that",
    ] {
        let Some(start) = normalized
            .to_ascii_lowercase()
            .find(&marker.to_ascii_lowercase())
        else {
            continue;
        };
        let candidate = normalized[start..]
            .split(['。', '！', '？', '.', '!', '?', '\n'])
            .next()
            .unwrap_or_default()
            .trim_matches(['，', ',', ':', '：', ' ']);
        if candidate.chars().count() >= 2 {
            let candidate = truncate(candidate, 180);
            if !facts.contains(&candidate) {
                facts.push(candidate);
            }
        }
    }
    facts
}

fn query_terms(query: &str) -> HashSet<String> {
    let normalized = normalize(query).to_lowercase();
    let chars = normalized.chars().collect::<Vec<_>>();
    let mut terms = HashSet::new();
    for word in normalized.split(|character: char| !character.is_alphanumeric()) {
        if word.chars().count() >= 2 {
            terms.insert(word.to_owned());
        }
    }
    for pair in chars.windows(2) {
        if pair.iter().all(|character| is_cjk(*character)) {
            terms.insert(pair.iter().collect());
        }
    }
    terms
}

fn memory_score(memory: &MemoryItem, terms: &HashSet<String>) -> f64 {
    let content = memory.content.to_lowercase();
    let hits = terms
        .iter()
        .filter(|term| content.contains(term.as_str()))
        .count() as f64;
    memory.weight + hits * 2.0
}

fn is_cjk(character: char) -> bool {
    ('\u{3400}'..='\u{9fff}').contains(&character)
}

#[derive(Deserialize)]
struct LegacyState {
    #[serde(default)]
    active_id: String,
    #[serde(default)]
    sessions: Vec<LegacySession>,
}

#[derive(Deserialize)]
struct LegacySession {
    id: String,
    #[serde(default = "legacy_default_title")]
    name: String,
    #[serde(default)]
    messages: Vec<LegacyMessage>,
    #[serde(default)]
    memories: Vec<LegacyMemory>,
}

#[derive(Deserialize)]
struct LegacyMessage {
    role: String,
    content: String,
}

#[derive(Deserialize)]
struct LegacyMemory {
    #[serde(rename = "type", default = "legacy_fact")]
    kind: String,
    content: String,
}

fn legacy_default_title() -> String {
    DEFAULT_TITLE.to_owned()
}

fn legacy_fact() -> String {
    "fact".to_owned()
}

fn legacy_data_candidates(config_dir: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = env::var_os("AMADEUS_DATA_DIR") {
        candidates.push(PathBuf::from(path));
    }
    if let Ok(current_dir) = env::current_dir() {
        candidates.push(current_dir.join("data"));
    }
    if let Ok(executable) = env::current_exe()
        && let Some(parent) = executable.parent()
    {
        candidates.push(parent.join("data"));
    }
    candidates.push(config_dir.join("data"));
    let mut seen = HashSet::new();
    candidates
        .into_iter()
        .filter(|path| seen.insert(path.clone()))
        .collect()
}

fn migrate_legacy(connection: &mut Connection, candidates: &[PathBuf]) -> Result<(), String> {
    for data_dir in candidates {
        let sessions = data_dir
            .join("characters")
            .join("kurisu")
            .join("sessions.json");
        import_legacy_sessions(connection, &sessions)?;
        import_legacy_memories(connection, &data_dir.join("memory.db"))?;
    }
    Ok(())
}

fn already_imported(connection: &Connection, path: &Path) -> Result<bool, String> {
    let source = path.to_string_lossy();
    connection
        .query_row(
            "SELECT 1 FROM legacy_imports WHERE source=?1",
            params![source.as_ref()],
            |_| Ok(()),
        )
        .optional()
        .map(|value| value.is_some())
        .map_err(db_error)
}

fn mark_imported(connection: &Connection, path: &Path) -> Result<(), String> {
    connection
        .execute(
            "INSERT OR IGNORE INTO legacy_imports(source, imported_at) VALUES (?1, ?2)",
            params![path.to_string_lossy().as_ref(), now_ms()],
        )
        .map_err(db_error)?;
    Ok(())
}

fn import_legacy_sessions(connection: &mut Connection, path: &Path) -> Result<(), String> {
    if already_imported(connection, path)? {
        return Ok(());
    }
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("读取旧版会话失败：{error}")),
    };
    let state: LegacyState =
        serde_json::from_slice(&bytes).map_err(|error| format!("旧版会话文件格式无效：{error}"))?;
    let transaction = connection.transaction().map_err(db_error)?;
    let now = now_ms();
    for (session_index, session) in state.sessions.iter().enumerate() {
        if validate_id(&session.id).is_err() {
            continue;
        }
        let timestamp = now.saturating_sub(session_index as i64);
        transaction
            .execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at)
                 VALUES (?1, ?2, ?3, ?3)",
                params![session.id, normalize(&session.name), timestamp],
            )
            .map_err(db_error)?;
        let existing: i64 = transaction
            .query_row(
                "SELECT COUNT(*) FROM messages WHERE session_id=?1",
                params![session.id],
                |row| row.get(0),
            )
            .map_err(db_error)?;
        if existing == 0 {
            for (message_index, message) in session.messages.iter().enumerate() {
                if !matches!(message.role.as_str(), "user" | "assistant")
                    || message.content.trim().is_empty()
                {
                    continue;
                }
                transaction
                    .execute(
                        "INSERT INTO messages(session_id, role, content, created_at)
                         VALUES (?1, ?2, ?3, ?4)",
                        params![
                            session.id,
                            message.role,
                            message.content,
                            timestamp.saturating_add(message_index as i64)
                        ],
                    )
                    .map_err(db_error)?;
            }
        }
        for memory in &session.memories {
            upsert_memory(
                &transaction,
                &memory.kind,
                &memory.content,
                "legacy-session",
                1.2,
                now,
            )?;
        }
    }
    if !state.active_id.is_empty()
        && transaction
            .query_row(
                "SELECT 1 FROM sessions WHERE id=?1",
                params![state.active_id],
                |_| Ok(()),
            )
            .optional()
            .map_err(db_error)?
            .is_some()
    {
        transaction
            .execute(
                "INSERT INTO app_state(key, value) VALUES ('active_session', ?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![state.active_id],
            )
            .map_err(db_error)?;
    }
    mark_imported(&transaction, path)?;
    transaction.commit().map_err(db_error)
}

fn import_legacy_memories(connection: &mut Connection, path: &Path) -> Result<(), String> {
    if !path.exists() || already_imported(connection, path)? {
        return Ok(());
    }
    let legacy = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|error| format!("打开旧版记忆库失败：{error}"))?;
    let exists = legacy
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hermes_memory'",
            [],
            |_| Ok(()),
        )
        .optional()
        .map_err(db_error)?
        .is_some();
    if !exists {
        mark_imported(connection, path)?;
        return Ok(());
    }
    let mut statement = legacy
        .prepare("SELECT kind, content, source, weight FROM hermes_memory")
        .map_err(db_error)?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, f64>(3)?,
            ))
        })
        .map_err(db_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(db_error)?;
    drop(statement);
    let transaction = connection.transaction().map_err(db_error)?;
    let now = now_ms();
    for (kind, content, source, weight) in rows {
        upsert_memory(
            &transaction,
            &kind,
            &content,
            &source,
            weight.clamp(0.1, 3.0),
            now,
        )?;
    }
    mark_imported(&transaction, path)?;
    transaction.commit().map_err(db_error)
}

fn validate_id(id: &str) -> Result<(), String> {
    if id.is_empty()
        || id.len() > 64
        || !id.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '-' || character == '_'
        })
    {
        return Err("会话 ID 无效".to_owned());
    }
    Ok(())
}

fn normalize(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn title_from(text: &str) -> String {
    let title = truncate(&normalize(text), 18);
    if title.is_empty() {
        DEFAULT_TITLE.to_owned()
    } else {
        title
    }
}

fn truncate(text: &str, max_chars: usize) -> String {
    let mut chars = text.chars();
    let value = chars.by_ref().take(max_chars).collect::<String>();
    if chars.next().is_some() {
        format!("{value}…")
    } else {
        value
    }
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

fn db_error(error: rusqlite::Error) -> String {
    format!("会话数据库错误：{error}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_store(name: &str) -> ConversationStore {
        let path = env::temp_dir().join(format!("amadeus-{name}-{}", Uuid::new_v4()));
        ConversationStore::new(path).expect("create test conversation store")
    }

    #[test]
    fn persists_turns_and_switches_sessions() {
        let store = temp_store("sessions");
        let first = store.active_id().unwrap();
        store.record_turn("我叫冈部", "知道了。", "chat").unwrap();
        assert_eq!(store.history_snapshot(10).unwrap().len(), 2);
        let second = store.create_session().unwrap().active_id;
        assert_ne!(first, second);
        assert!(store.history_snapshot(10).unwrap().is_empty());
        store.switch_session(&first).unwrap();
        assert_eq!(store.history_snapshot(10).unwrap()[0].content, "我叫冈部");
    }

    #[test]
    fn extracts_recalls_and_edits_memory() {
        let store = temp_store("memory");
        store
            .record_turn("请记住，我喜欢黑咖啡", "这种事我当然记得。", "chat")
            .unwrap();
        let recalled = store.memory_context("我平时喝什么", 8).unwrap();
        assert!(recalled.iter().any(|item| item.content.contains("黑咖啡")));
        let fact = store
            .list_memories()
            .unwrap()
            .into_iter()
            .find(|item| item.kind == "fact")
            .unwrap();
        let updated = store
            .update_memory(UpdateMemory {
                id: fact.id,
                content: "我喜欢无糖黑咖啡".to_owned(),
                weight: 2.0,
            })
            .unwrap();
        assert!(
            updated
                .iter()
                .any(|item| item.content == "我喜欢无糖黑咖啡")
        );
    }

    #[test]
    fn keeps_at_least_one_session() {
        let store = temp_store("minimum");
        let id = store.active_id().unwrap();
        assert!(store.delete_session(&id).is_err());
    }

    #[test]
    fn rejects_unsafe_session_ids() {
        let store = temp_store("ids");
        assert!(store.switch_session("../other").is_err());
        assert!(store.delete_session("x' OR 1=1 --").is_err());
    }

    #[test]
    fn concurrent_turn_stress_survives_reopen_without_partial_writes() {
        let directory = env::temp_dir().join(format!("amadeus-stress-{}", Uuid::new_v4()));
        let store = ConversationStore::new(directory.clone()).expect("create stress store");
        let expected_turns = 300usize;
        let mut workers = Vec::new();
        for worker in 0..4 {
            let store = store.clone();
            workers.push(std::thread::spawn(move || {
                for turn in 0..75 {
                    store
                        .record_turn(
                            &format!("worker {worker} turn {turn}"),
                            &format!("reply {worker}-{turn}"),
                            "stress",
                        )
                        .expect("record concurrent turn");
                }
            }));
        }
        for worker in workers {
            worker.join().expect("join stress worker");
        }
        drop(store);

        let reopened = ConversationStore::new(directory.clone()).expect("reopen stress store");
        assert_eq!(
            reopened.history_snapshot(expected_turns * 2).unwrap().len(),
            expected_turns * 2
        );
        assert_eq!(
            reopened.snapshot().unwrap().conversations[0].message_count,
            (expected_turns * 2) as u32
        );

        drop(reopened);
        let _ = fs::remove_dir_all(directory);
    }
}
