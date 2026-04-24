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
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ---------- Layout ---------- */
const AppLayout = {
  template: `
    <div class="sidebar" v-if="!isLogin">
      <div class="brand">Kiro Dashboard</div>
      <nav>
        <router-link to="/">总览</router-link>
        <router-link to="/agents">Agents</router-link>
        <router-link to="/skills">Skills</router-link>
        <router-link to="/events">Events</router-link>
        <router-link to="/scheduler">Scheduler</router-link>
        <router-link to="/config">Config</router-link>
      </nav>
      <div class="logout" @click="logout">退出登录</div>
    </div>
    <div :class="isLogin ? '' : 'main'">
      <router-view />
    </div>
  `,
  setup() {
    const route = VueRouter.useRoute();
    const router = VueRouter.useRouter();
    const isLogin = computed(() => route.path === "/login");
    async function logout() {
      await api("/logout", { method: "POST" }).catch(() => {});
      router.push("/login");
    }
    return { isLogin, logout };
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
        window.location.reload();
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
        <div class="card"><h3>Events</h3><div class="num">{{ counts.events }}</div></div>
        <div class="card"><h3>Active Jobs</h3><div class="num">{{ counts.jobs }}</div></div>
        <div class="card"><h3>Agents</h3><div class="num">{{ counts.agents }}</div></div>
        <div class="card"><h3>Skills</h3><div class="num">{{ counts.skills }}</div></div>
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
      <div class="toolbar">
        <select v-model="filter.severity"><option value="">全部严重级别</option><option>critical</option><option>high</option><option>medium</option><option>low</option></select>
        <input v-model="filter.source" placeholder="Source" />
        <input v-model="filter.q" placeholder="搜索标题/描述" @keyup.enter="load" />
        <button @click="load">查询</button>
        <button class="secondary" @click="reset">重置</button>
        <button @click="openModal()">新建</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>时间</th><th>标题</th><th>Source</th><th>Severity</th><th>描述</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="e in events" :key="e.id">
              <td>{{ e.id }}</td>
              <td>{{ e.ts }}</td>
              <td>{{ e.title }}</td>
              <td>{{ e.source }}</td>
              <td><span :class="'badge badge-' + e.severity">{{ e.severity }}</span></td>
              <td>{{ e.description }}</td>
              <td><button class="danger" @click="remove(e.id)">删除</button></td>
            </tr>
            <tr v-if="events.length === 0"><td colspan="7" class="empty">暂无数据</td></tr>
          </tbody>
        </table>
      </div>
      <!-- Modal -->
      <div class="modal-overlay" v-if="showModal" @click.self="closeModal">
        <div class="modal">
          <div class="modal-header"><h3>新建 Event</h3><button class="close" @click="closeModal">&times;</button></div>
          <div class="modal-body">
            <div class="field"><label>Title</label><input v-model="form.title" /></div>
            <div class="field"><label>Source</label><input v-model="form.source" /></div>
            <div class="field"><label>Severity</label>
              <select v-model="form.severity"><option>critical</option><option>high</option><option>medium</option><option>low</option></select>
            </div>
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
    const events = ref([]);
    const filter = reactive({ severity: "", source: "", q: "" });
    const showModal = ref(false);
    const form = reactive({ title: "", source: "", severity: "medium", description: "" });

    async function load() {
      const qs = new URLSearchParams();
      if (filter.severity) qs.append("severity", filter.severity);
      if (filter.source) qs.append("source", filter.source);
      if (filter.q) qs.append("q", filter.q);
      const data = await api("/events?" + qs.toString());
      events.value = data.events || [];
    }
    function reset() {
      filter.severity = "";
      filter.source = "";
      filter.q = "";
      load();
    }
    async function remove(id) {
      if (!confirm("确定删除?")) return;
      await api("/events/" + id, { method: "DELETE" });
      load();
    }
    function openModal() {
      form.title = "";
      form.source = "";
      form.severity = "medium";
      form.description = "";
      showModal.value = true;
    }
    function closeModal() { showModal.value = false; }
    async function save() {
      await api("/events", { method: "POST", body: form });
      closeModal();
      load();
    }
    onMounted(load);
    return { events, filter, load, reset, remove, showModal, form, openModal, closeModal, save };
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

/* ---------- ConfigPage ---------- */
const ConfigPage = {
  template: `
    <div>
      <h2 class="page-title">Config</h2>
      <div class="tabs">
        <button :class="{ active: tab === 'core' }" @click="tab = 'core'">Core Config</button>
        <button :class="{ active: tab === 'mappings' }" @click="tab = 'mappings'">Alert Mappings</button>
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
            <thead><tr><th>Source</th><th>Severity</th><th>Agent</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="(m, i) in mappings" :key="i">
                <td><input v-model="m.source" /></td>
                <td>
                  <select v-model="m.severity">
                    <option>critical</option><option>high</option><option>medium</option><option>low</option>
                  </select>
                </td>
                <td><input v-model="m.agent" /></td>
                <td><button class="danger" @click="removeMapping(i)">删除</button></td>
              </tr>
              <tr v-if="mappings.length === 0"><td colspan="4" class="empty">暂无映射</td></tr>
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

    async function load() {
      try {
        const c = await api("/config");
        Object.assign(core, c.config || {});
      } catch {}
      try {
        const m = await api("/mappings");
        mappings.value = m.mappings || [];
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
      mappings.value.push({ source: "", severity: "medium", agent: "" });
    }
    function removeMapping(i) {
      mappings.value.splice(i, 1);
    }
    onMounted(load);
    return { tab, core, mappings, saveCore, saveMappings, addMapping, removeMapping };
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
