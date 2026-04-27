const { createApp, ref, reactive, onMounted, computed } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

const BASE = "/api/dashboard";

const providerMeta = {
  aws: { types: ['ec2', 'rds'], classLabel: '机型', osLabel: 'OS/Engine' },
  tencent: { types: ['cvm', 'lighthouse'], classLabel: 'Class', osLabel: 'OS' }
};

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

/* ---------- Visual Helpers ---------- */
const AGENT_EMOJIS = ['🤖', '🕵️', '🧙‍♂️', '👨‍💻', '👩‍🔬', '🦸', '🧑‍🚀', '🧑‍⚕️', '🧑‍🌾', '🧑‍🔬'];

function simpleHash(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) - h) + str.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h);
}

function agentEmoji(name) {
  return AGENT_EMOJIS[simpleHash(name) % AGENT_EMOJIS.length];
}

function agentColor(name) {
  const h = simpleHash(name) % 360;
  return `hsl(${h}, 70%, 60%)`;
}

function agentTitle(toolsCount) {
  if (toolsCount === 0) return '见习者';
  if (toolsCount === 1) return '学徒';
  if (toolsCount <= 3) return '工匠';
  return '大师';
}

function skillColor(name) {
  const h = simpleHash(name) % 360;
  return `hsl(${h}, 65%, 55%)`;
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
          <router-link v-if="providers.aws?.enabled" to="/resources/aws" style="padding-left:28px;font-size:13px">AWS</router-link>
          <router-link v-if="providers.tencent?.enabled" to="/resources/tencent" style="padding-left:28px;font-size:13px">Tencent</router-link>
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
    const providers = ref({});
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
      try {
        const cfg = await api("/config");
        providers.value = cfg.providers || {};
      } catch (e) {
        providers.value = {};
      }
    });
    async function logout() {
      await api("/logout", { method: "POST" }).catch(() => {});
      authChecked.value = false;
      router.push("/login");
    }
    return { isLogin, logout, authChecked, providers };
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
        <div class="card card-accent-blue"><h3>📊 Events</h3><div class="num">{{ counts.events }}</div></div>
        <div class="card card-accent-green"><h3>⏰ Active Jobs</h3><div class="num">{{ counts.jobs }}</div></div>
        <div class="card card-accent-purple"><h3>🧑‍🚀 Agents</h3><div class="num">{{ counts.agents }}</div></div>
        <div class="card card-accent-orange"><h3>📚 Skills</h3><div class="num">{{ counts.skills }}</div></div>
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
        <div class="card-item persona-card" v-for="a in agents" :key="a.name" :style="{ borderColor: agentColor(a.name) }">
          <div class="persona-header">
            <div class="persona-avatar" :style="{ borderColor: agentColor(a.name), background: agentColor(a.name) + '18' }">
              <span class="persona-emoji">{{ agentEmoji(a.name) }}</span>
            </div>
            <div class="persona-info">
              <h4>{{ a.name }}</h4>
              <span class="persona-title">{{ agentTitle((a.tools || []).length) }}</span>
              <p>{{ a.description || "无描述" }}</p>
            </div>
          </div>
          <div class="persona-tools">
            <span class="tool-badge" v-for="t in (a.tools || [])" :key="t">{{ t }}</span>
            <span v-if="!(a.tools || []).length" class="tool-badge empty">无工具</span>
          </div>
          <div class="persona-stats">
            <div class="stat-bar-wrap">
              <span class="stat-label">Skills</span>
              <div class="stat-bar-bg">
                <div class="stat-bar-fill" :style="{ width: Math.min((a.linkedSkills || []).length * 20, 100) + '%', background: agentColor(a.name) }"></div>
              </div>
              <span class="stat-num">{{ (a.linkedSkills || []).length }}</span>
            </div>
          </div>
          <div class="persona-linked-skills" v-if="(a.linkedSkills || []).length">
            <span class="skill-link-tag" v-for="s in a.linkedSkills" :key="s.name">{{ s.name }}</span>
          </div>
          <div class="card-actions">
            <button class="btn-outline" @click="openManageModal(a.name)">管理 Skills</button>
          </div>
        </div>
      </div>
      <div class="empty" v-if="agents.length === 0">暂无数据</div>
      <!-- Manage Skills Modal -->
      <div class="modal-overlay" v-if="manageModal.show" @click.self="closeManageModal">
        <div class="modal">
          <div class="modal-header"><h3>管理 Skills — {{ manageModal.agentName }}</h3><button class="close" @click="closeManageModal">&times;</button></div>
          <div class="modal-body">
            <div class="field">
              <label>已关联 Skills</label>
              <div v-if="manageModal.linked.length === 0" style="color:#94a3b8">暂无关联 Skills</div>
              <div v-else style="display:flex;flex-wrap:wrap;gap:6px">
                <span v-for="s in manageModal.linked" :key="s.name" class="badge badge-tag" style="display:flex;align-items:center;gap:4px">
                  {{ s.name }}
                  <button style="background:none;border:none;color:#ef4444;cursor:pointer;padding:0;font-size:14px;line-height:1" @click="unlinkSkill(s.name)">&times;</button>
                </span>
              </div>
            </div>
            <div class="field">
              <label>添加 Skill</label>
              <div style="display:flex;gap:8px">
                <select v-model="manageModal.selectedSkill" style="flex:1">
                  <option value="">选择 Skill...</option>
                  <option v-for="s in availableSkills" :key="s.name" :value="s.name">{{ s.name }}</option>
                </select>
                <button class="btn-outline" @click="linkSkill">添加</button>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button class="secondary" @click="closeManageModal">关闭</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const agents = ref([]);
    const allSkills = ref([]);
    const manageModal = reactive({
      show: false,
      agentName: "",
      linked: [],
      selectedSkill: "",
    });

    async function loadAgents() {
      const data = await api("/agents");
      const agentList = data.agents || [];
      for (const a of agentList) {
        try {
          const sk = await api("/agents/" + encodeURIComponent(a.name) + "/skills");
          a.linkedSkills = sk.skills || [];
        } catch {
          a.linkedSkills = [];
        }
      }
      agents.value = agentList;
    }

    async function loadSkills() {
      const data = await api("/skills");
      allSkills.value = data.skills || [];
    }

    function openManageModal(agentName) {
      manageModal.agentName = agentName;
      manageModal.selectedSkill = "";
      const agent = agents.value.find(a => a.name === agentName);
      manageModal.linked = agent ? [...(agent.linkedSkills || [])] : [];
      manageModal.show = true;
    }

    function closeManageModal() {
      manageModal.show = false;
    }

    async function unlinkSkill(skillName) {
      await api("/agents/" + encodeURIComponent(manageModal.agentName) + "/skills/" + encodeURIComponent(skillName), { method: "DELETE" });
      manageModal.linked = manageModal.linked.filter(s => s.name !== skillName);
      loadAgents();
    }

    async function linkSkill() {
      if (!manageModal.selectedSkill) return;
      await api("/agents/" + encodeURIComponent(manageModal.agentName) + "/skills", { method: "POST", body: { skill_name: manageModal.selectedSkill } });
      manageModal.selectedSkill = "";
      const sk = await api("/agents/" + encodeURIComponent(manageModal.agentName) + "/skills");
      manageModal.linked = sk.skills || [];
      loadAgents();
    }

    const availableSkills = computed(() => {
      const linkedNames = new Set((manageModal.linked || []).map(s => s.name));
      return allSkills.value.filter(s => !linkedNames.has(s.name));
    });

    onMounted(async () => {
      await loadSkills();
      await loadAgents();
    });

    return { agents, allSkills, manageModal, openManageModal, closeManageModal, unlinkSkill, linkSkill, availableSkills, agentEmoji, agentColor, agentTitle };
  }
};

/* ---------- SkillsPage ---------- */
const SkillsPage = {
  template: `
    <div>
      <h2 class="page-title">Skills</h2>
      <div class="toolbar">
        <button @click="openModal()">新建 Skill</button>
      </div>
      <div class="card-grid">
        <div class="card-item book-card" v-for="s in skills" :key="s.name" :style="{ borderLeftColor: skillColor(s.name) }">
          <div class="book-spine" :style="{ background: skillColor(s.name) }"></div>
          <div class="book-content">
            <h4>📖 {{ s.name }}</h4>
            <p class="book-desc">{{ s.description || "无描述" }}</p>
            <div class="book-triggers">
              <span v-for="t in (s.triggers || [])" :key="t" class="bookmark-tag">{{ t }}</span>
              <span v-if="!(s.triggers || []).length" class="bookmark-tag empty">无触发器</span>
            </div>
            <div class="card-actions">
              <button class="btn-outline" @click="browse(s.name)">浏览</button>
              <button class="btn-danger-sm" @click="remove(s.name)">删除</button>
            </div>
          </div>
        </div>
      </div>
      <div class="empty" v-if="skills.length === 0">暂无数据</div>
      <!-- Create Modal -->
      <div class="modal-overlay" v-if="showModal" @click.self="closeModal">
        <div class="modal">
          <div class="modal-header"><h3>新建 Skill</h3><button class="close" @click="closeModal">&times;</button></div>
          <div class="modal-body">
            <div class="field"><label>Name *</label><input v-model="form.name" placeholder="仅允许字母、数字、-、_" /></div>
            <div class="field"><label>Description</label><input v-model="form.description" placeholder="Skill 描述" /></div>
          </div>
          <div class="modal-footer">
            <button class="secondary" @click="closeModal">取消</button>
            <button class="primary" @click="save">保存</button>
          </div>
        </div>
      </div>
      <!-- Browse Modal -->
      <div class="modal-overlay" v-if="browseModal.show" @click.self="closeBrowse">
        <div class="modal" style="width:720px">
          <div class="modal-header"><h3>{{ browseModal.name }}</h3><button class="close" @click="closeBrowse">&times;</button></div>
          <div class="modal-body">
            <div v-if="browseModal.loading" style="color:#64748b">加载中...</div>
            <div v-else-if="browseModal.error" style="color:#ef4444">{{ browseModal.error }}</div>
            <pre v-else class="skill-content">{{ browseModal.content }}</pre>
          </div>
          <div class="modal-footer">
            <button class="secondary" @click="closeBrowse">关闭</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const skills = ref([]);
    const showModal = ref(false);
    const form = reactive({ name: "", description: "" });
    const browseModal = reactive({ show: false, name: "", content: "", loading: false, error: "" });

    async function load() {
      const data = await api("/skills");
      skills.value = data.skills || [];
    }

    function openModal() {
      form.name = "";
      form.description = "";
      showModal.value = true;
    }

    function closeModal() { showModal.value = false; }

    async function save() {
      const name = form.name.trim();
      if (!name) { alert("Name 必填"); return; }
      await api("/skills", { method: "POST", body: { name, description: form.description.trim() } });
      closeModal();
      load();
    }

    async function remove(name) {
      if (!confirm(`确定删除 skill "${name}"?`)) return;
      await api("/skills/" + encodeURIComponent(name), { method: "DELETE" });
      load();
    }

    async function browse(name) {
      browseModal.name = name;
      browseModal.content = "";
      browseModal.error = "";
      browseModal.loading = true;
      browseModal.show = true;
      try {
        const data = await api("/skills/" + encodeURIComponent(name) + "/content");
        if (data.ok) browseModal.content = data.content;
        else browseModal.error = data.error || "加载失败";
      } catch (e) {
        browseModal.error = e.message || "加载失败";
      } finally {
        browseModal.loading = false;
      }
    }

    function closeBrowse() { browseModal.show = false; }

    onMounted(load);
    return { skills, showModal, form, browseModal, openModal, closeModal, save, remove, browse, closeBrowse, skillColor };
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
      <h2 class="page-title">Scheduler <span style="font-size:13px;font-weight:400;color:#64748b;margin-left:8px">时区：UTC+8 北京时间</span></h2>
      <div class="toolbar">
        <button @click="openModal()">新建</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>User</th><th>频率</th><th>时间 (UTC+8)</th><th>指令</th><th>启用</th><th>操作</th></tr></thead>
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
  props: ['provider'],
  template: `
    <div>
      <h2 class="page-title">Resources</h2>
      <div class="provider-tabs" v-if="Object.keys(enabledProviders).length > 1">
        <button
          v-for="(cfg, key) in enabledProviders"
          :key="key"
          :class="{ active: provider === key }"
          @click="switchProvider(key)"
        >{{ key.toUpperCase() }}</button>
      </div>
      <div class="toolbar">
        <select v-model="filterType" @change="load()">
          <option value="">全部类型</option>
          <option v-for="t in meta.types" :key="t" :value="t">{{ t.toUpperCase() }}</option>
        </select>
        <input v-model="searchQ" placeholder="搜索 Name / ID" />
        <select v-model="filterRegion">
          <option value="">全部 Region</option>
          <option v-for="reg in regions" :key="reg" :value="reg">{{ reg }}</option>
        </select>
        <input v-model="filterStatus" placeholder="Status" />
        <input v-model="filterClass" :placeholder="meta.classLabel" />
        <input v-model="filterOs" :placeholder="meta.osLabel" />
        <input v-model="filterTagKey" placeholder="Tag Key" />
        <input v-model="filterTagValue" placeholder="Tag Value" />
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#64748b;cursor:pointer">
          <input type="checkbox" v-model="onlyPinned" /> 仅看 Pinned
        </label>
        <button class="secondary" @click="resetFilters">重置</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width:40px">⭐</th>
              <th>Name</th>
              <th>Type</th>
              <th>Region</th>
              <th>{{ meta.classLabel }}</th>
              <th>{{ meta.osLabel }}</th>
              <th>ID</th>
              <th>Status</th>
              <th>Tags</th>
              <th style="width:120px">7d Trend</th>
              <th style="width:120px">7d Avg/P95/Max</th>
              <th style="width:120px">30d Avg/P95/Max</th>
              <th style="width:40px"></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in filteredResources" :key="r.id">
              <tr :class="{ pinned: isPinned(r.id) }">
                <td><button class="pin-btn" @click="togglePin(r.id)">{{ isPinned(r.id) ? '★' : '☆' }}</button></td>
                <td>{{ r.name }}</td>
                <td><span :class="'badge badge-' + r.type">{{ r.type }}</span></td>
                <td>{{ r.meta.region || '-' }}</td>
                <td>{{ r.class_type || '-' }}</td>
                <td>{{ r.os_or_engine || '-' }}</td>
                <td><code class="tag" :title="r.id" style="font-size:11px;max-width:140px;overflow:hidden;text-overflow:ellipsis;display:inline-block;white-space:nowrap;vertical-align:middle">{{ r.raw_id }}</code></td>
                <td>{{ r.status }}</td>
                <td>
                  <span v-for="(v, k) in r.tags" :key="k" class="badge badge-tag" :title="k + ': ' + v">
                    {{ k }}:{{ v }}
                  </span>
                  <span v-if="!r.tags || Object.keys(r.tags).length === 0" style="color:#cbd5e1">-</span>
                </td>
                <td v-html="sparklineSvg(r.sparkline, sparklineColor(r.type))"></td>
                <td>{{ formatStats(r.stats_7d) }}</td>
                <td>{{ formatStats(r.stats_30d) }}</td>
                <td><button class="pin-btn" @click="toggleExpand(r.id)">{{ expandedId === r.id ? '▼' : '▶' }}</button></td>
              </tr>
              <tr v-if="expandedId === r.id">
                <td colspan="13" style="background:#f8fafc;padding:16px">
                <div style="max-width:800px">
                  <div style="display:flex;gap:8px;margin-bottom:12px">
                    <button
                      v-for="rng in historyRanges"
                      :key="rng"
                      :class="{ active: historyRange === rng }"
                      @click="historyRange = rng; loadHistory(r.id, rng)"
                      style="padding:4px 12px;border:1px solid #cbd5e1;border-radius:4px;background:#fff;cursor:pointer"
                      :style="historyRange === rng ? 'background:#3b82f6;color:#fff;border-color:#3b82f6' : ''"
                    >{{ rng }}</button>
                  </div>
                  <div v-if="historyLoading" style="color:#64748b">加载中...</div>
                  <div v-else-if="historyData && historyData.error" style="color:#ef4444">{{ historyData.error }}</div>
                  <div v-else-if="historyData && historyData.ok">
                    <div style="font-size:12px;color:#64748b;margin-bottom:4px">
                      粒度: {{ historyData.granularity }}
                    </div>
                    <div v-html="historyChartSvg(historyData.data, sparklineColor(r.type))"></div>
                    <div style="display:flex;gap:24px;margin-top:8px;font-size:13px">
                      <span>MIN: <b>{{ historyData.stats.min != null ? historyData.stats.min + '%' : '-' }}</b></span>
                      <span>AVG: <b>{{ historyData.stats.avg != null ? historyData.stats.avg + '%' : '-' }}</b></span>
                      <span>P95: <b>{{ historyData.stats.p95 != null ? historyData.stats.p95 + '%' : '-' }}</b></span>
                      <span>MAX: <b>{{ historyData.stats.max != null ? historyData.stats.max + '%' : '-' }}</b></span>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
            </template>
            <tr v-if="filteredResources.length === 0"><td colspan="13" class="empty">暂无数据</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
  setup(props) {
    const router = VueRouter.useRouter();
    const provider = props.provider || 'aws';
    const meta = providerMeta[provider] || providerMeta.aws;
    const resources = ref([]);
    const pins = ref([]);
    const regions = ref([]);
    const enabledProviders = ref({});
    const filterType = ref("");
    const searchQ = ref("");
    const filterRegion = ref("");
    const filterStatus = ref("");
    const filterClass = ref("");
    const filterOs = ref("");
    const filterTagKey = ref("");
    const filterTagValue = ref("");
    const onlyPinned = ref(false);
    const expandedId = ref(null);
    const historyData = ref(null);
    const historyLoading = ref(false);
    const historyRange = ref("24h");
    const historyRanges = ["24h", "7d", "30d", "180d"];

    async function loadHistory(resourceId, range) {
      historyLoading.value = true;
      historyData.value = null;
      try {
        const data = await api(`/resources/${encodeURIComponent(resourceId)}/history?range=${range}`);
        historyData.value = data;
      } catch (e) {
        historyData.value = { error: e.message };
      } finally {
        historyLoading.value = false;
      }
    }

    function toggleExpand(id) {
      if (expandedId.value === id) {
        expandedId.value = null;
        historyData.value = null;
      } else {
        expandedId.value = id;
        loadHistory(id, historyRange.value);
      }
    }

    function historyChartSvg(data, color) {
      if (!data || data.length < 2) return '<span style="color:#cbd5e1">-</span>';
      const values = data.map(d => d.value != null ? d.value : d.avg_value);
      const valid = values.filter(v => v != null);
      if (valid.length < 2) return '<span style="color:#cbd5e1">-</span>';
      const min = Math.min(...valid), max = Math.max(...valid);
      const range = max - min || 1;
      const pts = values.map((v, i) => {
        if (v == null) return "";
        const x = (i / (values.length - 1)) * 100;
        const y = 60 - ((v - min) / range) * 60;
        return `${x},${y}`;
      }).filter(Boolean).join(" ");
      return `<svg viewBox="0 0 100 60" width="100%" height="120" style="display:block"><polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
    }

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
      return { ec2: "#3b82f6", rds: "#8b5cf6", eks: "#f59e0b", cvm: "#3b82f6", lighthouse: "#8b5cf6" }[type] || "#94a3b8";
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
    function resetFilters() {
      searchQ.value = "";
      filterRegion.value = "";
      filterStatus.value = "";
      filterClass.value = "";
      filterOs.value = "";
      filterTagKey.value = "";
      filterTagValue.value = "";
      onlyPinned.value = false;
    }
    async function loadProviders() {
      try {
        const cfg = await api("/config");
        const all = cfg.providers || {};
        const enabled = {};
        for (const [k, v] of Object.entries(all)) {
          if (v && v.enabled) enabled[k] = v;
        }
        enabledProviders.value = enabled;
      } catch {
        enabledProviders.value = {};
      }
    }
    function switchProvider(key) {
      if (key !== provider) {
        router.push(`/resources/${key}`);
      }
    }
    async function load(refresh = false) {
      const qs = new URLSearchParams();
      if (refresh) qs.append("refresh", "1");
      if (filterType.value) qs.append("type", filterType.value);
      const data = await api(`/resources?provider=${provider}&${qs.toString()}`);
      resources.value = data.resources || [];
      pins.value = data.pinned || [];
      regions.value = data.regions || [];
      reorder();
    }
    const filteredResources = computed(() => {
      let list = resources.value;
      if (onlyPinned.value) list = list.filter(r => isPinned(r.id));

      const q = searchQ.value.trim().toLowerCase();
      if (q) list = list.filter(r => (r.name + r.raw_id).toLowerCase().includes(q));

      const region = filterRegion.value.trim().toLowerCase();
      if (region) list = list.filter(r => (r.meta.region || '').toLowerCase().includes(region));

      const status = filterStatus.value.trim().toLowerCase();
      if (status) list = list.filter(r => r.status.toLowerCase().includes(status));

      const cls = filterClass.value.trim().toLowerCase();
      if (cls) {
        list = list.filter(r => (r.class_type || '').toLowerCase().includes(cls));
      }

      const os = filterOs.value.trim().toLowerCase();
      if (os) {
        list = list.filter(r => (r.os_or_engine || '').toLowerCase().includes(os));
      }

      const tagKey = filterTagKey.value.trim();
      const tagValue = filterTagValue.value.trim();
      if (tagKey) {
        list = list.filter(r => {
          const tags = r.tags || {};
          if (!(tagKey in tags)) return false;
          if (tagValue) return tags[tagKey] === tagValue;
          return true;
        });
      }

      return list;
    });

    onMounted(() => { loadProviders(); load(); });
    return { meta, resources, pins, regions, filterType, searchQ, filterRegion, filterStatus, filterClass, filterOs, filterTagKey, filterTagValue, onlyPinned, isPinned, togglePin, sparklineSvg, sparklineColor, formatStats, resetFilters, filteredResources, load, expandedId, historyData, historyLoading, historyRange, historyRanges, toggleExpand, loadHistory, historyChartSvg, enabledProviders, switchProvider, provider };
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
            <thead><tr><th>Source</th><th>Service</th><th>Severity</th><th>Agent</th><th>Skill</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="(m, i) in mappings" :key="i">
                <td>
                  <select v-model="m.source">
                    <option value="">- 选择 Source -</option>
                    <option v-for="s in sourceOptions" :key="s" :value="s">{{ s }}</option>
                  </select>
                </td>
                <td>
                  <select v-model="m.service">
                    <option value="">- 全部服务 -</option>
                    <option v-for="s in mappingServiceOptions" :key="s" :value="s">{{ s }}</option>
                  </select>
                </td>
                <td>
                  <select v-model="m.severity">
                    <option value="">- 选择级别 -</option>
                    <option>critical</option><option>high</option><option>medium</option><option>low</option>
                  </select>
                </td>
                <td>
                  <select v-model="m.agent" @change="onAgentChange(m)">
                    <option value="">- 选择 Agent -</option>
                    <option v-for="a in agentOptions" :key="a" :value="a">{{ a }}</option>
                  </select>
                </td>
                <td>
                  <select v-model="m.skill" :disabled="!m.agent">
                    <option value="">- 选择 Skill -</option>
                    <option v-for="s in (agentSkillsMap[m.agent] || [])" :key="s" :value="s">{{ s }}</option>
                  </select>
                </td>
                <td><button class="btn-danger-sm" @click="removeMapping(i)">删除</button></td>
              </tr>
              <tr v-if="mappings.length === 0"><td colspan="6" class="empty">暂无映射</td></tr>
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
                <td><button class="btn-danger-sm" @click="removeServiceRule(i)">删除</button></td>
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
    const agents = ref([]);
    const skills = ref([]);
    const agentSkillsMap = reactive({});

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
      try {
        const a = await api("/agents");
        agents.value = a.agents || [];
        // Load each agent's skills in parallel
        const skillPromises = agents.value.map(async agent => {
          try {
            const sk = await api("/agents/" + encodeURIComponent(agent.name) + "/skills");
            agentSkillsMap[agent.name] = (sk.skills || []).map(s => s.name);
          } catch {
            agentSkillsMap[agent.name] = [];
          }
        });
        await Promise.all(skillPromises);
      } catch {}
      try {
        const s = await api("/skills");
        skills.value = s.skills || [];
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
      mappings.value.push({ source: "", service: "", severity: "", agent: "", skill: "" });
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
      for (const m of mappings.value) {
        if (m.service) set.add(m.service);
      }
      return Array.from(set).sort();
    });
    const sourceOptions = computed(() => {
      const set = new Set(["prometheus", "jenkins", "cloudwatch", "manual"]);
      for (const m of mappings.value) {
        if (m.source) set.add(m.source);
      }
      return Array.from(set).sort();
    });
    const agentOptions = computed(() => {
      return agents.value.map(a => a.name).sort();
    });
    function onAgentChange(m) {
      const allowed = agentSkillsMap[m.agent] || [];
      if (m.skill && !allowed.includes(m.skill)) {
        m.skill = "";
      }
    }
    onMounted(load);
    return { tab, core, mappings, serviceRules, agentSkillsMap, mappingServiceOptions, sourceOptions, agentOptions, saveCore, saveMappings, addMapping, removeMapping, onAgentChange, saveServiceRules, addServiceRule, removeServiceRule };
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
  { path: "/resources", redirect: "/resources/aws" },
  { path: "/resources/:provider(aws|tencent)", component: ResourcesPage, props: true },
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
