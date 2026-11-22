import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';
import { z } from 'zod';
import { projectPlanner, shiftDay } from '../shared/planner';
import { TodoTree } from '../domain/tree';
import type { CanvasItem, CanvasSource, CanvasSyncReport } from '../shared/canvas';
import { validateDrafts } from '../shared/drafts';
import { completionConfigSchema, createTodoSchema, defaultCompletionConfig, dueSchema, type CompletionConfig, type CreateTodoInput, type Mutation, type Todo, type TodoDraft, type TodoPatch } from '../shared/todo';

const instant = z.string().datetime({ offset: true });
const todoSchema = z.strictObject({
  id: z.string().uuid(), parentId: z.string().uuid().nullable(), title: z.string().trim().min(1).max(200),
  notes: z.string().max(10000), completed: z.boolean(), due: dueSchema,
  completedAt: instant.nullable().optional(),
  createdAt: instant, updatedAt: instant, revision: z.number().int().positive(),
  deletedAt: instant.nullable(), trashBatchId: z.string().uuid().nullable(),
}).refine(t => (t.deletedAt === null) === (t.trashBatchId === null), '回收批次与删除状态不一致').refine(t => !t.completedAt || t.completed, '完成时间必须对应已完成状态');
const daySchema = z.string().refine(day => { try { shiftDay(day, 0); return true; } catch { return false; } }, '无效日期');
const dayOrderSchema = z.strictObject({ day:daySchema, ids:z.array(z.string().uuid()).refine(ids => new Set(ids).size === ids.length, '排序存在重复 ID') });
const canvasMappingSchema = z.strictObject({sourceId:z.string().regex(/^[a-f0-9]{64}$/),key:z.string().min(1).max(1000),todoId:z.string().uuid()});
const canvasSourceFields = {description:z.string().max(5 * 1024 * 1024),url:z.string().max(8000).refine(value => { if (!value) return true; try { const url=new URL(value); return ['https:','http:'].includes(url.protocol) && !url.username && !url.password; } catch { return false; } }, '无效的Canvas原文地址')};
const canvasRecordSchema = canvasMappingSchema.extend(canvasSourceFields);
const canvasItemSchema = z.strictObject({key:z.string().min(1).max(1000),title:createTodoSchema.shape.title,...canvasSourceFields,due:dueSchema.refine(due => due.mode !== 'inherit', 'Canvas事项不能继承截止时间')});
const backupFields = { exportedAt: instant, todos: z.array(todoSchema), completionConfig: completionConfigSchema };
const backupSchema = z.discriminatedUnion('version', [
  z.strictObject({ version:z.literal(2), ...backupFields }),
  z.strictObject({ version:z.literal(3), ...backupFields, dayOrders:z.array(dayOrderSchema) }),
  z.strictObject({ version:z.literal(4), ...backupFields, dayOrders:z.array(dayOrderSchema), canvasItems:z.array(canvasMappingSchema) }),
  z.strictObject({ version:z.literal(5), ...backupFields, dayOrders:z.array(dayOrderSchema), canvasItems:z.array(canvasRecordSchema) }),
  z.strictObject({ version:z.literal(6), ...backupFields, dayOrders:z.array(dayOrderSchema), canvasItems:z.array(canvasRecordSchema) }),
]);
export type TodoBackup = z.infer<typeof backupSchema>;

/** Every write loads and validates one tree under an IMMEDIATE transaction. */
export class TaskRepository {
  private db: DatabaseSync;
  constructor(path: string) {
    this.db = new DatabaseSync(path);
    try {
      this.db.exec('PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 3000;');
      const version = Number(this.db.prepare('PRAGMA user_version').get()!.user_version);
      const tables = this.db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all().map(r => String(r.name)).sort();
      if (version === 1) throw new Error('旧版 v1 数据库需要显式备份和迁移；原数据未修改。');
      if (version !== 0 && version !== 2 && version !== 3 && version !== 4 && version !== 5 && version !== 6) throw new Error('不支持的数据库版本，请使用匹配版本或显式迁移。');
      if (version === 0 && tables.length) throw new Error('数据库包含未知表，拒绝初始化或覆盖。');
      if (version === 0) this.initialize();
      if (version === 2 || version === 3) {
        this.db.exec('BEGIN IMMEDIATE');
        try {
          this.verifySchema(version === 3, false);
          const tree = this.loadTree();
          if (version === 3) this.validateOrders(this.readOrders(), tree.list(true));
          if (version === 2) this.db.exec("CREATE TABLE day_orders (day TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)));");
          this.db.exec("CREATE TABLE canvas_items (source_id TEXT NOT NULL, item_key TEXT NOT NULL, todo_id TEXT NOT NULL UNIQUE REFERENCES todos(id) DEFERRABLE INITIALLY DEFERRED, description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', PRIMARY KEY(source_id,item_key)); PRAGMA user_version=5;");
          this.verifySchema();
          this.db.exec('COMMIT');
        } catch (error) { this.db.exec('ROLLBACK'); throw error; }
      }
      if (version === 4) {
        this.db.exec('BEGIN IMMEDIATE');
        try {
          this.verifySchema(true, true, false);
          const tree = this.loadTree();
          this.validateOrders(this.readOrders(), tree.list(true));
          const oldMappings = this.db.prepare('SELECT source_id,item_key,todo_id FROM canvas_items').all().map(row => canvasMappingSchema.parse({sourceId:row.source_id,key:row.item_key,todoId:row.todo_id}));
          this.validateCanvasMappings(oldMappings, tree.list(true));
          this.db.exec("ALTER TABLE canvas_items ADD COLUMN description TEXT NOT NULL DEFAULT ''; ALTER TABLE canvas_items ADD COLUMN url TEXT NOT NULL DEFAULT ''; PRAGMA user_version=5;");
          this.verifySchema(); this.db.exec('COMMIT');
        } catch (error) { this.db.exec('ROLLBACK'); throw error; }
      }
      if (Number(this.db.prepare('PRAGMA user_version').get()!.user_version) === 5) {
        this.db.exec('BEGIN IMMEDIATE');
        try {
          // 旧数据没有完成时刻：已完成的待办以最后一次更新时间作为完成日期兜底。
          this.db.exec("ALTER TABLE todos ADD COLUMN completed_at TEXT; UPDATE todos SET completed_at=updated_at WHERE completed=1; PRAGMA user_version=6;");
          this.verifySchema();
          this.db.exec('COMMIT');
        } catch (error) { this.db.exec('ROLLBACK'); throw error; }
      }
      this.verifySchema();
      const tree = this.loadTree();
      this.validateOrders(this.readOrders(), tree.list(true));
      this.validateCanvasMappings(this.readCanvasMappings(), tree.list(true));
    } catch (error) { this.db.close(); throw error; }
  }
  private initialize() {
    this.db.exec(`BEGIN IMMEDIATE;
      CREATE TABLE todos (
        id TEXT PRIMARY KEY NOT NULL, parent_id TEXT REFERENCES todos(id) DEFERRABLE INITIALLY DEFERRED,
        title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200), notes TEXT NOT NULL,
        completed INTEGER NOT NULL CHECK(completed IN (0,1)), due_json TEXT NOT NULL CHECK(json_valid(due_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
        deleted_at TEXT, trash_batch_id TEXT, completed_at TEXT,
        CHECK((deleted_at IS NULL) = (trash_batch_id IS NULL)));
      CREATE INDEX todos_parent ON todos(parent_id, deleted_at);
      CREATE INDEX todos_trash ON todos(trash_batch_id);
      CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)));
      CREATE TABLE draft_confirmations (token TEXT PRIMARY KEY NOT NULL, fingerprint TEXT NOT NULL, count INTEGER NOT NULL CHECK(count >= 0));
      CREATE TABLE day_orders (day TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)));
      CREATE TABLE canvas_items (source_id TEXT NOT NULL, item_key TEXT NOT NULL, todo_id TEXT NOT NULL UNIQUE REFERENCES todos(id) DEFERRABLE INITIALLY DEFERRED, description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', PRIMARY KEY(source_id,item_key));
      PRAGMA user_version = 6;`);
    try {
      this.db.prepare('INSERT INTO metadata VALUES (?, ?)').run('completionConfig', JSON.stringify(defaultCompletionConfig));
      this.db.exec('COMMIT');
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  private verifySchema(withOrders = true, withCanvas = true, withContent = true) {
    const schemaVersion = Number(this.db.prepare('PRAGMA user_version').get()!.user_version);
    const todoColumns = ['id','parent_id','title','notes','completed','due_json','created_at','updated_at','revision','deleted_at','trash_batch_id','completed_at'];
    const expected: Record<string,string[]> = {
      todos: schemaVersion >= 6 ? todoColumns : todoColumns.slice(0, -1),
      metadata: ['key','value'], draft_confirmations: ['token','fingerprint','count'],
    };
    if (withOrders) expected.day_orders = ['day','value'];
    if (withCanvas) expected.canvas_items = withContent ? ['source_id','item_key','todo_id','description','url'] : ['source_id','item_key','todo_id'];
    const actual = this.db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all().map(r => String(r.name)).sort();
    if (JSON.stringify(actual) !== JSON.stringify(Object.keys(expected).sort())) throw new Error('数据库表结构不匹配，拒绝写入。');
    if (this.db.prepare("SELECT name FROM sqlite_master WHERE type='trigger'").all().length) throw new Error('数据库包含未知触发器，拒绝写入。');
    for (const [table, columns] of Object.entries(expected)) {
      const fields = this.db.prepare(`PRAGMA table_info(${table})`).all();
      const got = fields.map(r => String(r.name));
      if (JSON.stringify(got) !== JSON.stringify(columns)) throw new Error(`数据库 ${table} 字段不匹配。`);
      for (const field of fields) {
        const integer = ['completed','revision','count'].includes(String(field.name));
        const optional = ['parent_id','deleted_at','trash_batch_id','completed_at'].includes(String(field.name));
        const primary = table === 'canvas_items' ? (field.name === 'source_id' ? 1 : field.name === 'item_key' ? 2 : 0) : Number(['id','key','token','day'].includes(String(field.name)));
        if (String(field.type).toUpperCase() !== (integer ? 'INTEGER' : 'TEXT') || Number(field.notnull) !== Number(!optional) || Number(field.pk) !== primary) throw new Error(`数据库 ${table} 字段约束不匹配。`);
      }
    }
    const foreign = this.db.prepare('PRAGMA foreign_key_list(todos)').all();
    if (foreign.length !== 1 || foreign[0].table !== 'todos' || foreign[0].from !== 'parent_id' || foreign[0].to !== 'id') throw new Error('数据库父引用约束不匹配。');
    if (withCanvas) {
      const foreignCanvas = this.db.prepare('PRAGMA foreign_key_list(canvas_items)').all();
      if (foreignCanvas.length !== 1 || foreignCanvas[0].table !== 'todos' || foreignCanvas[0].from !== 'todo_id' || foreignCanvas[0].to !== 'id') throw new Error('Canvas来源引用约束不匹配。');
    }
    if (this.db.prepare('PRAGMA foreign_key_check').all().length) throw new Error('数据库存在无效父引用。');
    for (const row of this.db.prepare('SELECT * FROM draft_confirmations').all()) {
      z.strictObject({ token:z.string().trim().min(1).max(200), fingerprint:z.string().regex(/^[a-f0-9]{64}$/), count:z.number().int().min(1).max(100) }).parse(row);
    }
    if (this.db.prepare('PRAGMA quick_check').get()!.quick_check !== 'ok') throw new Error('数据库完整性检查失败。');
    const meta = this.db.prepare('SELECT key FROM metadata').all();
    if (meta.length !== 1 || meta[0].key !== 'completionConfig') throw new Error('数据库配置缺失或包含未知配置。');
  }
  private loadTree(): TodoTree {
    const todos = this.db.prepare('SELECT * FROM todos ORDER BY rowid').all().map(r => {
      if (r.completed !== 0 && r.completed !== 1) throw new Error('数据库完成状态无效。');
      return todoSchema.parse({ id:r.id, parentId:r.parent_id, title:r.title, notes:r.notes, completed:r.completed === 1,
        completedAt:r.completed_at ?? null,
        due:JSON.parse(String(r.due_json)), createdAt:r.created_at, updatedAt:r.updated_at, revision:r.revision,
        deletedAt:r.deleted_at, trashBatchId:r.trash_batch_id });
    });
    return new TodoTree(todos, this.getConfig());
  }
  private persist(tree: TodoTree, before: Todo[]) {
    const previous = new Map(before.map(t => [t.id, t]));
    const insert = this.db.prepare('INSERT INTO todos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
    const update = this.db.prepare('UPDATE todos SET parent_id=?,title=?,notes=?,completed=?,due_json=?,created_at=?,updated_at=?,revision=?,deleted_at=?,trash_batch_id=?,completed_at=? WHERE id=? AND revision=?');
    for (const t of tree.list(true)) {
      const old = previous.get(t.id);
      const values = [t.parentId,t.title,t.notes,Number(t.completed),JSON.stringify(t.due),t.createdAt,t.updatedAt,t.revision,t.deletedAt,t.trashBatchId,t.completedAt ?? null];
      if (!old) insert.run(t.id, ...values);
      else if (JSON.stringify(old) !== JSON.stringify(t)) {
        if (t.revision <= old.revision) throw new Error('领域变更缺少版本递增。');
        if (Number(update.run(...values,t.id,old.revision).changes) !== 1) throw new Error('版本冲突，请刷新后重试。');
      }
    }
    const rootIds = new Set(tree.list(true).filter(todo => todo.parentId === null).map(todo => todo.id));
    for (const order of this.readOrders()) {
      const ids = order.ids.filter(id => rootIds.has(id));
      if (ids.length !== order.ids.length) this.db.prepare('UPDATE day_orders SET value=? WHERE day=?').run(JSON.stringify(ids),order.day);
    }
    this.db.prepare("UPDATE metadata SET value=? WHERE key='completionConfig'").run(JSON.stringify(tree.getConfig()));
  }
  private transaction<T>(action: (tree: TodoTree) => T): Mutation<T> {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const tree = this.loadTree();
      const before = tree.list(true);
      const value = action(tree);
      this.persist(tree, before);
      const warnings = tree.warnings();
      this.db.exec('COMMIT');
      return { value, warnings };
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  private readOrders(): {day:string;ids:string[]}[] {
    return this.db.prepare('SELECT day,value FROM day_orders ORDER BY day').all().map(row => dayOrderSchema.parse({day:row.day,ids:JSON.parse(String(row.value))}));
  }
  private validateOrders(orders: {day:string;ids:string[]}[], todos: Todo[]) {
    const roots = new Set(todos.filter(todo => todo.parentId === null).map(todo => todo.id));
    if (new Set(orders.map(order => order.day)).size !== orders.length) throw new Error('排序日期重复');
    for (const order of orders) if (order.ids.some(id => !roots.has(id))) throw new Error('备份排序包含不存在的任务或子项');
  }
  getDayOrder(day: string): string[] {
    daySchema.parse(day);
    const row = this.db.prepare('SELECT value FROM day_orders WHERE day=?').get(day);
    return row ? z.array(z.string().uuid()).parse(JSON.parse(String(row.value))) : [];
  }
  setDayOrder(day: string, ids: string[]): string[] {
    const order = dayOrderSchema.parse({day,ids});
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const expected = projectPlanner(this.loadTree().list(true)).dailyRoots(day).map(todo => todo.id);
      const supplied = new Set(order.ids);
      if (expected.length !== order.ids.length || expected.some(id => !supplied.has(id))) throw new Error('当日任务列表已变化，或排序包含子项、缺项或多项，请刷新后重新排序。');
      this.db.prepare('INSERT INTO day_orders VALUES (?,?) ON CONFLICT(day) DO UPDATE SET value=excluded.value').run(day,JSON.stringify(order.ids));
      this.db.exec('COMMIT'); return [...order.ids];
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  private readCanvasMappings() {
    return this.db.prepare('SELECT source_id,item_key,todo_id,description,url FROM canvas_items ORDER BY source_id,item_key').all().map(row => canvasRecordSchema.parse({sourceId:row.source_id,key:row.item_key,todoId:row.todo_id,description:row.description,url:row.url}));
  }
  private validateCanvasMappings(mappings: z.infer<typeof canvasMappingSchema>[], todos: Todo[]) {
    const ids = new Set(todos.map(todo => todo.id));
    const pairs = new Set<string>(), mapped = new Set<string>();
    for (const mapping of mappings) {
      const pair = JSON.stringify([mapping.sourceId,mapping.key]);
      if (!ids.has(mapping.todoId) || pairs.has(pair) || mapped.has(mapping.todoId)) throw new Error('Canvas来源映射重复或引用不存在事项。');
      pairs.add(pair); mapped.add(mapping.todoId);
    }
  }
  getCanvasSourceIds(): string[] { return [...new Set(this.readCanvasMappings().map(mapping => mapping.sourceId))]; }
  getCanvasSource(todoId: string): CanvasSource | null {
    z.string().uuid().parse(todoId);
    const row = this.db.prepare('SELECT description,url FROM canvas_items WHERE todo_id=?').get(todoId);
    return row ? z.object(canvasSourceFields).parse(row) : null;
  }
  syncCanvas(sourceId: string, items: CanvasItem[]): CanvasSyncReport {
    z.string().regex(/^[a-f0-9]{64}$/).parse(sourceId);
    const parsed = z.array(canvasItemSchema).max(5000).parse(items);
    if (new Set(parsed.map(item => item.key)).size !== parsed.length) throw new Error('Canvas日历存在重复事项标识。');
    return this.transaction(tree => {
      const report: CanvasSyncReport = {added:0,updated:0,unchanged:0,skipped:0,changes:[]};
      const find = this.db.prepare('SELECT todo_id,description,url FROM canvas_items WHERE source_id=? AND item_key=?');
      const insert = this.db.prepare('INSERT INTO canvas_items VALUES (?,?,?,?,?)');
      for (const item of parsed) {
        const mapping = find.get(sourceId,item.key);
        if (!mapping) {
          const saved = tree.create({title:item.title,notes:'',due:item.due});
          insert.run(sourceId,item.key,saved.id,item.description,item.url);
          report.added++; report.changes.push({id:saved.id,title:saved.title,kind:'added',due:saved.due});
          continue;
        }
        const previous = tree.get(String(mapping.todo_id));
        if (previous.deletedAt) { report.skipped++; continue; }
        const descriptionChanged = mapping.description !== item.description, urlChanged = mapping.url !== item.url;
        const todoChanged = previous.title !== item.title || JSON.stringify(previous.due) !== JSON.stringify(item.due);
        if (!todoChanged && !descriptionChanged && !urlChanged) { report.unchanged++; continue; }
        const saved = todoChanged ? tree.update(previous.id,{title:item.title,due:item.due},previous.revision) : previous;
        this.db.prepare('UPDATE canvas_items SET description=?,url=? WHERE source_id=? AND item_key=?').run(item.description,item.url,sourceId,item.key);
        report.updated++; report.changes.push({id:saved.id,title:saved.title,kind:'updated',previousTitle:previous.title,previousDue:previous.due,due:saved.due,descriptionChanged,urlChanged});
      }
      return report;
    }).value;
  }
  list(includeDeleted = false) { return this.loadTree().list(includeDeleted); }
  get(id: string) { return this.loadTree().get(id); }
  effectiveDue(id: string) { return this.loadTree().effectiveDue(id); }
  warnings() { return this.loadTree().warnings(); }
  create(input: CreateTodoInput) { return this.transaction(t => t.create(input)); }
  editTodo(id: string, patch: TodoPatch, completed: boolean, expectedRevision: number) {
    z.boolean().parse(completed);
    return this.transaction(tree => {
      const saved=tree.update(id,patch,expectedRevision);
      if(saved.completed!==completed) tree.setCompleted(id,completed,saved.revision);
      return tree.get(id);
    });
  }
  update(id: string, patch: TodoPatch, expectedRevision: number) { return this.transaction(t => t.update(id, patch, expectedRevision)); }
  move(id: string, parentId: string | null, expectedRevision: number) { return this.transaction(t => t.move(id, parentId, expectedRevision)); }
  setCompleted(id: string, completed: boolean, expectedRevision: number) { return this.transaction(t => t.setCompleted(id, completed, expectedRevision)); }
  trash(id: string, expectedRevision: number) { return this.transaction(t => t.trash(id, expectedRevision)); }
  restore(batchId: string) { return this.transaction(t => t.restore(batchId)); }
  getConfig(): CompletionConfig {
    const row = this.db.prepare("SELECT value FROM metadata WHERE key='completionConfig'").get();
    if (!row) throw new Error('缺少完成联动配置。');
    return completionConfigSchema.parse(JSON.parse(String(row.value)));
  }
  setConfig(config: CompletionConfig): Mutation<CompletionConfig> {
    const valid = completionConfigSchema.parse(config);
    return this.transaction(t => { t.setConfig(valid); return t.getConfig(); });
  }
  hasDraftConfirmation(token: string): boolean {
    z.string().trim().min(1).max(200).parse(token);
    return !!this.db.prepare('SELECT token FROM draft_confirmations WHERE token=?').get(token);
  }
  saveDrafts(drafts: TodoDraft[], token: string): Mutation<number> {
    const parsed = validateDrafts(drafts);
    z.string().trim().min(1).max(200).parse(token);
    const fingerprint = createHash('sha256').update(JSON.stringify(parsed)).digest('hex');
    return this.transaction(tree => {
      const seen = this.db.prepare('SELECT fingerprint,count FROM draft_confirmations WHERE token=?').get(token);
      if (seen) {
        if (seen.fingerprint !== fingerprint) throw new Error('该确认令牌已用于不同草稿，请重新确认。');
        return Number(seen.count);
      }
      const real = new Map<string,string>();
      for (const draft of parsed) {
        const parentId = draft.parentTempId === null ? null : real.get(draft.parentTempId) ?? draft.parentTodoId ?? null;
        if (draft.parentTempId !== null && parentId === null) throw new Error('草稿父项尚未保存，请刷新后重试。');
        const input = createTodoSchema.parse({ title:draft.title, notes:draft.notes, due:draft.due, parentId });
        real.set(draft.tempId,tree.create(input,draft.todoId).id);
      }
      this.db.prepare('INSERT INTO draft_confirmations VALUES (?, ?, ?)').run(token,fingerprint,parsed.length);
      return parsed.length;
    });
  }
  exportBackup(): TodoBackup {
    // One snapshot for todos and config, even when another process writes.
    this.db.exec('BEGIN');
    try {
      const tree = this.loadTree();
      const result: TodoBackup = { version:6, dayOrders:this.readOrders(), canvasItems:this.readCanvasMappings(), exportedAt:new Date().toISOString(), todos:tree.list(true), completionConfig:tree.getConfig() };
      this.db.exec('COMMIT'); return result;
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  importBackup(value: unknown): Mutation<number> {
    const backup = backupSchema.parse(value);
    const replacement = new TodoTree(backup.todos, backup.completionConfig);
    const orders = backup.version >= 3 && 'dayOrders' in backup ? backup.dayOrders : [];
    const mappings = !('canvasItems' in backup) ? [] : backup.version === 4 ? backup.canvasItems.map(mapping => ({...mapping,description:'',url:''})) : backup.canvasItems;
    this.validateCanvasMappings(mappings, backup.todos);
    this.validateOrders(orders, backup.todos);
    this.db.exec('BEGIN IMMEDIATE');
    try {
      if (this.loadTree().list(true).length || this.db.prepare('SELECT token FROM draft_confirmations LIMIT 1').get() || this.db.prepare('SELECT day FROM day_orders LIMIT 1').get() || this.db.prepare('SELECT todo_id FROM canvas_items LIMIT 1').get()) throw new Error('备份只能导入全新空数据库，原数据库未修改。');
      this.persist(replacement, []);
      for (const order of orders) this.db.prepare('INSERT INTO day_orders VALUES (?,?)').run(order.day,JSON.stringify(order.ids));
      for (const mapping of mappings) this.db.prepare('INSERT INTO canvas_items VALUES (?,?,?,?,?)').run(mapping.sourceId,mapping.key,mapping.todoId,mapping.description,mapping.url);
      const warnings = replacement.warnings();
      this.db.exec('COMMIT');
      return { value:backup.todos.length, warnings };
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  close() { this.db.close(); }
}
