const { createApp, ref, reactive, onMounted, computed } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

const BASE = "/api/dashboard";

async function api(path, opts = {}) {
  opts.headers = opts.headers || {};
  opts.headers["Content-Type"] = opts.headers["Content-Type"] || "application/json";
  opts.credentials = "include";
  if (opts.body && typeof opts.body === "object") {
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(BASE + path, opts);
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    window.location.href = "/dashboard/#/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ---------- Layout ---------- */
const AppLayout = {
  template: `
    <div v-if="!authChecked" class="login-wrap">
      <div class="login-box"><p>加载中...</p></div>
    </div>
    <template v-else>
      <div class="sidebar" v-if="!isLogin">
        <div class="brand">Kiro Dashboard</div>
        <nav>
          <router-link to="/">总览</router-link>
          <router-link to="/agents">Agents</router-link>
          <router-link to="/skills">Skills</router-link>
          <router-link to="/events">Events</router-link>
          <router-link to="/scheduler">Scheduler</router-link>
          <router-link to="/resources">Resources</router-link>
          <router-link to="/config">Config</router-link>
        </nav>
        <div class="logout" @click="logout">退出登录</div>
      </div>
      <div :class="isLogin ? '' : 'main'">
        <router-view />
      </div>
    </template>
  `,
  setup() {
    const route = VueRouter.useRoute();
    const router = VueRouter.useRouter();
    const isLogin = computed(() => route.path === "/login");
    const authChecked = ref(false);
    onMounted(async () => {
      if (isLogin.value) {
        authChecked.value = true;
        return;
      }
      try {
        await api("/agents");
        authChecked.value = true;
      } catch (e) {
        authChecked.value = true;
      }
    });
    async function logout() {
      await api("/logout", { method: "POST" }).catch(() => {});
      authChecked.value = false;
      router.push("/login");
    }
    return { isLogin, logout, authChecked };
  }
};

/* ---------- LoginPage ---------- */
const LoginPage = {
  template: `
    <div class="login-wrap">
      <div class="login-box">
        <h2>Dashboard 登录</h2>
        <input type="password" v-model="token" @keyup.enter="login" placeholder="输入访问令牌" />
        <button @click="login">登录</button>
        <div class="err" v-if="error">{{ error }}</div>
      </div>
    </div>
  `,
  setup() {
    const token = ref("");
    const error = ref("");
    async function login() {
      error.value = "";
      try {
        await api("/auth", { method: "POST", body: { token: token.value } });
        window.location.href = "/dashboard/#/";
      } catch (e) {
        error.value = e.message || "登录失败";
      }
    }
    return { token, error, login };
  }
};

/* ---------- OverviewPage ---------- */
const OverviewPage = {
  template: `
    <div>
      <h2 class="page-title">总览</h2>
      <div class="cards">
        <div class="card card-accent-blue"><h3>Events</h3><div class="num">{{ counts.events }}</div></div>
        <div class="card card-accent-green"><h3>Active Jobs</h3><div class="num">{{ counts.jobs }}</div></div>
        <div class="card card-accent-purple"><h3>Agents</h3><div class="num">{{ counts.agents }}</div></div>
        <div class="card card-accent-orange"><h3>Skills</h3><div class="num">{{ counts.skills }}</div></div>
      </div>
    </div>
  `,
  setup() {
    const counts = reactive({ events: 0, jobs: 0, agents: 0, skills: 0 });
    onMounted(async () => {
      try {
        const [ev, ag, sk, sch] = await Promise.all([
          api("/events?limit=1"),
          api("/agents"),
          api("/skills"),
          api("/scheduler"),
        ]);
        counts.events = ev.events?.length ?? 0; // approximate; real count not exposed, use first page
        // Better: count via length if small, else keep 0. Let's try to get total by fetching with large limit
      } catch {}
      try {
        const evAll = await api("/events?limit=9999");
        counts.events = evAll.events?.length ?? 0;
      } catch {}
      try {
        const agAll = await api("/agents");
        counts.agents = agAll.agents?.length ?? 0;
      } catch {}
      try {
        const skAll = await api("/skills");
        counts.skills = skAll.skills?.length ?? 0;
      } catch {}
      try {
        const schAll = await api("/scheduler");
        counts.jobs = schAll.jobs?.filter(j => j.enabled).length ?? 0;
      } catch {}
    });
    return { counts };
  }
};

/* ---------- AgentsPage ---------- */
const AgentsPage = {
  template: `
    <div>
      <h2 class="page-title">Agents</h2>
      <div class="card-grid">
        <div class="card-item" v-for="a in agents" :key="a.name">
          <h4>{{ a.name }}</h4>
          <p>{{ a.description || "无描述" }}</p>
          <div class="meta">Tools: {{ (a.tools || []).join(", ") || "-" }}</div>
        </div>
      </div>
      <div class="empty" v-if="agents.length === 0">暂无数据</div>
    </div>
  `,
  setup() {
    const agents = ref([]);
    onMounted(async () => {
      const data = await api("/agents");
      agents.value = data.agents || [];
    });
    return { agents };
  }
};

/* ---------- SkillsPage ---------- */
const SkillsPage = {
  template: `
    <div>
      <h2 class="page-title">Skills</h2>
      <div class="card-grid">
        <div class="card-item" v-for="s in skills" :key="s.name">
          <h4>{{ s.name }}</h4>
          <p>{{ s.description || "无描述" }}</p>
          <div class="meta">Triggers: {{ (s.triggers || []).join(", ") || "-" }}</div>
        </div>
      </div>
      <div class="empty" v-if="skills.length === 0">暂无数据</div>
    </div>
  `,
  setup() {
    const skills = ref([]);
    onMounted(async () => {
      const data = await api("/skills");
      skills.value = data.skills || [];
    });
    return { skills };
  }
};

/* ---------- EventsPage ---------- */
const EventsPage = {
  template: `
    <div>
      <h2 class="page-title">Events</h2>

      <!-- 说明卡片 -->
      <div class="info-cards">
        <div class="info-card">
          <h4>🚨 分级响应标准</h4>
          <p><span class="badge badge-critical">critical</span> <span class="badge badge-high">high</span> → 自动触发 Kiro <code>ec2-alert-analyzer</code> 分析 + 飞书主动推送</p>
          <p><span class="badge badge-medium">medium</span> <span class="badge badge-low">low</span> → 仅入库，不触发自动分析</p>
        </div>
        <div class="info-card">
          <h4>🏷️ Event Type 判断</h4>
          <p><b>Webhook 推送</b>：由外部系统（Prometheus / Jenkins / CloudWatch 等）在 payload 中 <code>event_type</code> 字段指定</p>
          <p><b>手动录入</b>：<code>/event 类型=xxx</code> 指定；未指定时默认为「手动记录」</p>
        </div>
      </div>

      <div class="toolbar">
        <!-- 时间段 -->
        <select v-model="timeRange" @change="onTimeChange">
          <option value="">全部时间</option>
          <option value="7d">一周</option>
          <option value="30d">一个月</option>
          <option value="90d">三个月</option>
          <option value="custom">自定义</option>
        </select>
        <template v-if="timeRange === 'custom'">
          <input type="date" v-model="customStart" />
          <span style="color:#94a3b8">~</span>
          <input type="date" v-model="customEnd" />
        </template>
        <!-- 服务名 -->
        <select v-model="serviceFilter">
          <option value="">全部服务</option>
          <option v-for="s in serviceOptions" :key="s" :value="s">{{ s }}</option>
        </select>
        <!-- Entities -->
        <select v-model="entityFilter">
          <option value="">全部实体</option>
          <option v-for="e in entityOptions" :key="e" :value="e">{{ e }}</option>
        </select>
        <!-- 原有筛选 -->
        <select v-model="filter.severity"><option value="">全部严重级别</option><option>critical</option><option>high</option><option>medium</option><option>low</option></select>
        <input v-model="filter.source" placeholder="Source" />
        <input v-model="filter.q" placeholder="搜索标题/描述" @keyup.enter="loadEvents" />
        <button @click="loadEvents">查询</button>
        <button class="secondary" @click="reset">重置</button>
        <button @click="openModal()">新建</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>标题</th><th>Type</th><th>Source</th><th>Severity</th><th>Entities</th><th>描述</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="e in displayEvents" :key="e.id">
              <td>{{ e.ts }}</td>
              <td>{{ e.title }}</td>
              <td>{{ e.event_type || "-" }}</td>
              <td>{{ e.source }}</td>
              <td><span :class="'badge badge-' + e.severity">{{ e.severity }}</span></td>
              <td><code class="tag">{{ fmtEntityPair(e) }}</code></td>
              <td>{{ e.description }}</td>
              <td><button class="danger" @click="remove(e.id)">删除</button></td>
            </tr>
            <tr v-if="displayEvents.length === 0"><td colspan="8" class="empty">暂无数据</td></tr>
          </tbody>
        </table>
      </div>
      <!-- Modal -->
      <div class="modal-overlay" v-if="showModal" @click.self="closeModal">
        <div class="modal">
          <div class="modal-header"><h3>新建 Event</h3><button class="close" @click="closeModal">&times;</button></div>
          <div class="modal-body">
            <div class="field"><label>Title *</label><input v-model="form.title" /></div>
            <div class="field"><label>Event Type *</label><input v-model="form.event_type" placeholder="如: 指标异常 / 应用发版 / 系统变更" /></div>
            <div class="field"><label>Source</label><input v-model="form.source" placeholder="如: prometheus / jenkins" /></div>
            <div class="field"><label>Severity</label>
              <select v-model="form.severity"><option>critical</option><option>high</option><option>medium</option><option>low</option></select>
            </div>
            <div class="field"><label>Entities（逗号分隔）</label><input v-model="form.entities_raw" placeholder="如: test1, node-exporter" /></div>
            <div class="field"><label>Description</label><textarea v-model="form.description"></textarea></div>
          </div>
          <div class="modal-footer">
            <button class="secondary" @click="closeModal">取消</button>
            <button class="primary" @click="save">保存</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const allEvents = ref([]);
    const filter = reactive({ severity: "", source: "", q: "" });
    const timeRange = ref("");
    const customStart = ref("");
    const customEnd = ref("");
    const serviceFilter = ref("");
    const entityFilter = ref("");
    const showModal = ref(false);
    const form = reactive({ title: "", event_type: "", source: "", severity: "medium", description: "", entities_raw: "" });
    const serviceRules = ref([]);

    function fmtServiceName(event) {
      for (const rule of serviceRules.value) {
        const field = rule.field === "type" ? "event_type" : rule.field;
        const fieldVal = (event[field] || "").toLowerCase();
        if (fieldVal.includes((rule.keyword || "").toLowerCase())) {
          return rule.service;
        }
      }
      const source = (event.source || "").toLowerCase();
      return source ? source.charAt(0).toUpperCase() + source.slice(1) : "-";
    }
    function fmtEntityName(event) {
      let entities = event.entities;
      if (!entities) {
        const m = (event.title || "").match(/^(\S+)/);
        return m ? m[1] : "-";
      }
      if (typeof entities === "string") {
        try { entities = JSON.parse(entities); } catch { return entities; }
      }
      if (Array.isArray(entities) && entities.length > 0) return entities[0];
      return "-";
    }
    function fmtEntityPair(event) {
      const svc = fmtServiceName(event);
      const name = fmtEntityName(event);
      return `(${svc}, ${name})`;
    }
    function getDateRange() {
      const now = new Date();
      const fmt = d => d.toISOString().slice(0, 10);
      if (timeRange.value === "7d") {
        return [fmt(new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)), fmt(now)];
      }
      if (timeRange.value === "30d") {
        return [fmt(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)), fmt(now)];
      }
      if (timeRange.value === "90d") {
        return [fmt(new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000)), fmt(now)];
      }
      if (timeRange.value === "custom") {
        return [customStart.value, customEnd.value];
      }
      return ["", ""];
    }
    function onTimeChange() {
      if (timeRange.value !== "custom") {
        customStart.value = "";
        customEnd.value = "";
      }
    }
    const serviceOptions = computed(() => {
      const set = new Set(serviceRules.value.map(r => r.service).filter(Boolean));
      return Array.from(set).sort();
    });
    const entityOptions = computed(() => {
      const set = new Set();
      for (const e of allEvents.value) {
        let entities = e.entities;
        if (typeof entities === "string") {
          try { entities = JSON.parse(entities); } catch { continue; }
        }
        if (Array.isArray(entities)) entities.forEach(ent => set.add(ent));
      }
      return Array.from(set).sort();
    });
    const displayEvents = computed(() => {
      return allEvents.value.filter(e => {
        if (serviceFilter.value && fmtServiceName(e) !== serviceFilter.value) return false;
        if (entityFilter.value) {
          let entities = e.entities;
          if (typeof entities === "string") {
            try { entities = JSON.parse(entities); } catch { return false; }
          }
          if (!Array.isArray(entities) || !entities.includes(entityFilter.value)) return false;
        }
        return true;
      });
    });
    async function loadEvents() {
      const qs = new URLSearchParams();
      if (filter.severity) qs.append("severity", filter.severity);
      if (filter.source) qs.append("source", filter.source);
      if (filter.q) qs.append("q", filter.q);
      const [start, end] = getDateRange();
      if (start) qs.append("start_date", start);
      if (end) qs.append("end_date", end);
      const data = await api("/events?" + qs.toString());
      allEvents.value = data.events || [];
    }
    function reset() {
      filter.severity = "";
      filter.source = "";
      filter.q = "";
      timeRange.value = "";
      customStart.value = "";
      customEnd.value = "";
      serviceFilter.value = "";
      entityFilter.value = "";
      loadEvents();
    }
    async function remove(id) {
      if (!confirm("确定删除?")) return;
      await api("/events/" + id, { method: "DELETE" });
      loadEvents();
    }
    function openModal() {
      form.title = "";
      form.event_type = "";
      form.source = "";
      form.severity = "medium";
      form.description = "";
      form.entities_raw = "";
      showModal.value = true;
    }
    function closeModal() { showModal.value = false; }
    async function save() {
      const body = {
        title: form.title,
        event_type: form.event_type,
        source: form.source || "manual",
        severity: form.severity,
        description: form.description,
        entities: form.entities_raw ? form.entities_raw.split(",").map(s => s.trim()).filter(Boolean) : [],
      };
      await api("/events", { method: "POST", body });
      closeModal();
      loadEvents();
    }
    onMounted(async () => {
      try {
        const sr = await api("/service-rules");
        serviceRules.value = sr.rules || [];
      } catch {}
      loadEvents();
    });
    return { allEvents, displayEvents, filter, timeRange, customStart, customEnd, serviceFilter, entityFilter, serviceOptions, entityOptions, loadEvents, reset, remove, showModal, form, openModal, closeModal, save, fmtEntityPair, onTimeChange };
  }
};

/* ---------- SchedulerPage ---------- */
const SchedulerPage = {
  template: `
    <div>
      <h2 class="page-title">Scheduler</h2>
      <div class="toolbar">
        <button @click="openModal()">新建</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>User</th><th>频率</th><th>时间</th><th>指令</th><th>启用</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="j in jobs" :key="j.id">
              <td>{{ j.id }}</td>
              <td>{{ j.user_id }}</td>
              <td>{{ j.frequency }}</td>
              <td>{{ j.time_str }}</td>
              <td>{{ j.prompt }}</td>
              <td><input type="checkbox" class="toggle" :checked="j.enabled" @change="toggle(j, $event.target.checked)" /></td>
              <td>
                <button @click="openModal(j)">编辑</button>
                <button class="danger" @click="remove(j.id)">删除</button>
              </td>
            </tr>
            <tr v-if="jobs.length === 0"><td colspan="7" class="empty">暂无数据</td></tr>
          </tbody>
        </table>
      </div>
      <!-- Modal -->
      <div class="modal-overlay" v-if="showModal" @click.self="closeModal">
        <div class="modal">
          <div class="modal-header"><h3>{{ editingId ? '编辑 Job' : '新建 Job' }}</h3><button class="close" @click="closeModal">&times;</button></div>
          <div class="modal-body">
            <div class="field"><label>User ID</label><input v-model="form.user_id" /></div>
            <div class="field"><label>频率</label>
              <select v-model="form.frequency">
                <option>每天</option><option>每周一</option><option>每周二</option><option>每周三</option>
                <option>每周四</option><option>每周五</option><option>每周六</option><option>每周日</option><option>工作日</option>
              </select>
            </div>
            <div class="field"><label>时间 (HH:MM)</label><input v-model="form.time_str" /></div>
            <div class="field"><label>指令</label><textarea v-model="form.prompt"></textarea></div>
          </div>
          <div class="modal-footer">
            <button class="secondary" @click="closeModal">取消</button>
            <button class="primary" @click="save">保存</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const jobs = ref([]);
    const showModal = ref(false);
    const editingId = ref(null);
    const form = reactive({ user_id: "system", frequency: "每天", time_str: "09:00", prompt: "" });

    async function load() {
      const data = await api("/scheduler");
      jobs.value = data.jobs || [];
    }
    async function toggle(j, enabled) {
      await api("/scheduler/" + j.id, { method: "PUT", body: { enabled } });
      load();
    }
    async function remove(id) {
      if (!confirm("确定删除?")) return;
      await api("/scheduler/" + id, { method: "DELETE" });
      load();
    }
    function openModal(job = null) {
      if (job) {
        editingId.value = job.id;
        form.user_id = job.user_id;
        form.frequency = job.frequency;
        form.time_str = job.time_str;
        form.prompt = job.prompt;
      } else {
        editingId.value = null;
        form.user_id = "system";
        form.frequency = "每天";
        form.time_str = "09:00";
        form.prompt = "";
      }
      showModal.value = true;
    }
    function closeModal() { showModal.value = false; }
    async function save() {
      if (editingId.value) {
        await api("/scheduler/" + editingId.value, { method: "PUT", body: { frequency: form.frequency, time_str: form.time_str, prompt: form.prompt } });
      } else {
        await api("/scheduler", { method: "POST", body: form });
      }
      closeModal();
      load();
    }
    onMounted(load);
    return { jobs, showModal, editingId, form, load, toggle, remove, openModal, closeModal, save };
  }
};

/* ---------- ResourcesPage ---------- */
const ResourcesPage = {
  template: `
    <div>
      <h2 class="page-title">Resources</h2>
      <div class="toolbar">
        <button @click="load(true)" title="刷新">🔃</button>
        <select v-model="filterType" @change="load()">
          <option value="">全部类型</option>
          <option value="ec2">EC2</option>
          <option value="rds">RDS</option>
        </select>
        <input v-model="searchQ" placeholder="搜索 Name / ID" />
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#64748b;cursor:pointer">
          <input type="checkbox" v-model="onlyPinned" /> 仅看 Pinned
        </label>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width:40px">⭐</th>
              <th>Name</th>
              <th>Type</th>
              <th>Region</th>
              <th>机型</th>
              <th>OS</th>
              <th>ID</th>
              <th>Status</th>
              <th style="width:120px">7d Trend</th>
              <th style="width:120px">7d Avg/P95/Max</th>
              <th style="width:120px">30d Avg/P95/Max</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in filteredResources" :key="r.id" :class="{ pinned: isPinned(r.id) }">
              <td><button class="pin-btn" @click="togglePin(r.id)">{{ isPinned(r.id) ? '★' : '☆' }}</button></td>
              <td>{{ r.name }}</td>
              <td><span :class="'badge badge-' + r.type">{{ r.type }}</span></td>
              <td>{{ r.meta.region || '-' }}</td>
              <td>{{ r.type === 'ec2' ? (r.meta.instance_type || '-') : (r.meta.db_instance_class || '-') }}</td>
              <td>{{ r.type === 'ec2' ? (r.meta.os || '-') : (r.meta.engine || '-') }}</td>
              <td><code class="tag">{{ r.raw_id }}</code></td>
              <td>{{ r.status }}</td>
              <td v-html="sparklineSvg(r.sparkline, sparklineColor(r.type))"></td>
              <td>{{ formatStats(r.stats_7d) }}</td>
              <td>{{ formatStats(r.stats_30d) }}</td>
            </tr>
            <tr v-if="filteredResources.length === 0"><td colspan="11" class="empty">暂无数据</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
  setup() {
    const resources = ref([]);
    const pins = ref([]);
    const filterType = ref("");
    const searchQ = ref("");
    const onlyPinned = ref(false);

    function isPinned(id) { return pins.value.includes(id); }
    async function togglePin(id) {
      const idx = pins.value.indexOf(id);
      if (idx >= 0) pins.value.splice(idx, 1);
      else pins.value.push(id);
      await api("/resources/pins", { method: "POST", body: { pins: pins.value } });
      reorder();
    }
    function reorder() {
      resources.value.sort((a, b) => {
        const pa = isPinned(a.id) ? -1 : 1;
        const pb = isPinned(b.id) ? -1 : 1;
        return pa - pb;
      });
    }
    function sparklineColor(type) {
      return { ec2: "#3b82f6", rds: "#8b5cf6", eks: "#f59e0b" }[type] || "#94a3b8";
    }
    function sparklineSvg(points, color) {
      if (!points || points.length < 2) return '<span style="color:#cbd5e1">-</span>';
      const valid = points.filter(v => v != null);
      if (valid.length < 2) return '<span style="color:#cbd5e1">-</span>';
      const min = Math.min(...valid), max = Math.max(...valid);
      const range = max - min || 1;
      const pts = points.map((v, i) => {
        if (v == null) return "";
        const x = (i / (points.length - 1)) * 100;
        const y = 30 - ((v - min) / range) * 30;
        return `${x},${y}`;
      }).filter(Boolean).join(" ");
      return `<svg viewBox="0 0 100 30" width="100" height="30" style="display:block"><polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
    }
    function formatStats(stats) {
      if (!stats || stats.avg == null) return '-';
      return `${stats.avg}% / ${stats.p95}% / ${stats.max}%`;
    }
    async function load(refresh = false) {
      const qs = new URLSearchParams();
      if (refresh) qs.append("refresh", "1");
      if (filterType.value) qs.append("type", filterType.value);
      const data = await api("/resources?" + qs.toString());
      resources.value = data.resources || [];
      pins.value = data.pinned || [];
      reorder();
    }
    const filteredResources = computed(() => {
      let list = resources.value;
      if (onlyPinned.value) list = list.filter(r => isPinned(r.id));
      const q = searchQ.value.trim().toLowerCase();
      if (q) list = list.filter(r => (r.name + r.raw_id).toLowerCase().includes(q));
      return list;
    });

    onMounted(() => load());
    return { resources, pins, filterType, searchQ, onlyPinned, isPinned, togglePin, sparklineSvg, sparklineColor, formatStats, filteredResources, load };
  }
};

/* ---------- ConfigPage ---------- */
const ConfigPage = {
  template: `
    <div>
      <h2 class="page-title">Config</h2>
      <div class="tabs">
        <button :class="{ active: tab === 'core' }" @click="tab = 'core'">Core Config</button>
        <button :class="{ active: tab === 'mappings' }" @click="tab = 'mappings'">Alert Mappings</button>
        <button :class="{ active: tab === 'service_rules' }" @click="tab = 'service_rules'">Service Rules</button>
      </div>
      <div v-if="tab === 'core'">
        <div class="toolbar"><button @click="saveCore">保存</button></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Key</th><th>Value</th></tr></thead>
            <tbody>
              <tr v-for="(v, k) in core" :key="k">
                <td>{{ k }}</td>
                <td><input v-model="core[k]" style="width:100%" /></td>
              </tr>
              <tr v-if="Object.keys(core).length === 0"><td colspan="2" class="empty">暂无配置</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-if="tab === 'mappings'">
        <div class="toolbar">
          <button @click="addMapping">添加</button>
          <button class="secondary" @click="saveMappings">保存 Mappings</button>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Source</th><th>Service</th><th>Severity</th><th>Agent</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="(m, i) in mappings" :key="i">
                <td><input v-model="m.source" /></td>
                <td>
                  <select v-model="m.service">
                    <option value="">- 全部服务 -</option>
                    <option v-for="s in mappingServiceOptions" :key="s" :value="s">{{ s }}</option>
                  </select>
                </td>
                <td>
                  <select v-model="m.severity">
                    <option>critical</option><option>high</option><option>medium</option><option>low</option>
                  </select>
                </td>
                <td><input v-model="m.agent" /></td>
                <td><button class="danger" @click="removeMapping(i)">删除</button></td>
              </tr>
              <tr v-if="mappings.length === 0"><td colspan="5" class="empty">暂无映射</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-if="tab === 'service_rules'">
        <div class="toolbar">
          <button @click="addServiceRule">添加</button>
          <button class="secondary" @click="saveServiceRules">保存 Rules</button>
        </div>
        <div class="info-card" style="margin-bottom:12px">
          <p>按<strong>顺序</strong>匹配第一条满足的规则。Field 可选 title / source / event_type，Keyword 支持部分匹配（不区分大小写）。</p>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>顺序</th><th>Field</th><th>Keyword</th><th>Service</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in serviceRules" :key="i">
                <td>{{ i + 1 }}</td>
                <td>
                  <select v-model="r.field">
                    <option value="title">Title</option>
                    <option value="source">Source</option>
                    <option value="event_type">Event Type</option>
                  </select>
                </td>
                <td><input v-model="r.keyword" /></td>
                <td><input v-model="r.service" /></td>
                <td><button class="danger" @click="removeServiceRule(i)">删除</button></td>
              </tr>
              <tr v-if="serviceRules.length === 0"><td colspan="5" class="empty">暂无规则</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `,
  setup() {
    const tab = ref("core");
    const core = reactive({});
    const mappings = ref([]);
    const serviceRules = ref([]);

    async function load() {
      try {
        const c = await api("/config");
        Object.assign(core, c.config || {});
      } catch {}
      try {
        const m = await api("/mappings");
        mappings.value = m.mappings || [];
      } catch {}
      try {
        const sr = await api("/service-rules");
        serviceRules.value = sr.rules || [];
      } catch {}
    }
    async function saveCore() {
      await api("/config", { method: "POST", body: core });
      alert("已保存");
    }
    async function saveMappings() {
      await api("/mappings", { method: "POST", body: { mappings: mappings.value } });
      alert("已保存");
    }
    function addMapping() {
      mappings.value.push({ source: "", service: "", severity: "medium", agent: "" });
    }
    function removeMapping(i) {
      mappings.value.splice(i, 1);
    }
    async function saveServiceRules() {
      await api("/service-rules", { method: "POST", body: { rules: serviceRules.value } });
      alert("已保存");
    }
    function addServiceRule() {
      serviceRules.value.push({ field: "title", keyword: "", service: "" });
    }
    function removeServiceRule(i) {
      serviceRules.value.splice(i, 1);
    }
    const mappingServiceOptions = computed(() => {
      const set = new Set();
      for (const r of serviceRules.value) {
        if (r.service) set.add(r.service);
      }
      // Also include services already used in mappings
      for (const m of mappings.value) {
        if (m.service) set.add(m.service);
      }
      return Array.from(set).sort();
    });
    onMounted(load);
    return { tab, core, mappings, serviceRules, mappingServiceOptions, saveCore, saveMappings, addMapping, removeMapping, saveServiceRules, addServiceRule, removeServiceRule };
  }
};

/* ---------- Router ---------- */
const routes = [
  { path: "/login", component: LoginPage },
  { path: "/", component: OverviewPage },
  { path: "/agents", component: AgentsPage },
  { path: "/skills", component: SkillsPage },
  { path: "/events", component: EventsPage },
  { path: "/scheduler", component: SchedulerPage },
  { path: "/resources", component: ResourcesPage },
  { path: "/config", component: ConfigPage },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

/* ---------- App ---------- */
const app = createApp(AppLayout);
app.use(router);
app.mount("#app");
