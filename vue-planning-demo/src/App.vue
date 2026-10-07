<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import {
  ApiError,
  authenticate,
  checkHealth,
  createPlanningRun,
  getItinerary,
  getRun,
  getRunEvents,
} from "./api";
import type { ItineraryEnvelope, RunPublic, TripFocus } from "./types";

const session = ref<{ username: string; token: string } | null>(null);
const authMode = ref<"login" | "register">("register");
const credentials = reactive({ username: "", password: "" });
const form = reactive({
  destination: "南京",
  startDate: "",
  days: 2,
  focus: "sights_first" as TripFocus,
  attractions: "历史古迹、博物馆",
  food: "",
  pace: "每天不超过 3 个景点，留出休息时间",
});
const busy = ref(false);
const run = ref<RunPublic | null>(null);
const progress = ref("填写旅行条件后开始规划");
const itinerary = ref<ItineraryEnvelope | null>(null);
const error = ref("");
const apiHealthy = ref<boolean | null>(null);
const sequence = ref(0);
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let healthTimer: ReturnType<typeof setInterval> | undefined;
let active = true;

const endDate = computed(() => {
  if (!form.startDate || !form.days) return "";
  const date = new Date(`${form.startDate}T12:00:00`);
  date.setDate(date.getDate() + Number(form.days) - 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
});
async function refreshHealth() {
  try {
    apiHealthy.value = (await checkHealth()).status === "ok";
  } catch {
    apiHealthy.value = false;
  }
}

onMounted(() => {
  void refreshHealth();
  healthTimer = setInterval(() => void refreshHealth(), 30_000);
});

onBeforeUnmount(() => {
  active = false;
  if (pollTimer) clearTimeout(pollTimer);
  if (healthTimer) clearInterval(healthTimer);
});

async function submitAuth() {
  error.value = "";
  try {
    const result = await authenticate(
      authMode.value,
      credentials.username.trim(),
      credentials.password,
    );
    session.value = { username: result.username, token: result.token };
  } catch (cause) {
    error.value = messageOf(cause);
  }
}

function messageOf(cause: unknown) {
  return cause instanceof Error ? cause.message : "发生未知错误，请重试。";
}

async function startPlanning() {
  if (!session.value) return;
  busy.value = true;
  error.value = "";
  itinerary.value = null;
  run.value = null;
  sequence.value = 0;
  progress.value = "正在创建规划任务…";
  try {
    const query = `${form.destination} ${form.days} 日游，${form.attractions || "按当地代表性景点安排"}；${form.pace}`;
    const created = await createPlanningRun(session.value.token, {
      kind: "travel_plan",
      request: {
        query,
        destination: form.destination.trim(),
        start_date: form.startDate,
        end_date: endDate.value,
        days: Number(form.days),
        trip_focus: form.focus,
        attraction_preference: form.attractions.trim(),
        food_preference: form.food.trim(),
        habit_preference: form.pace.trim(),
      },
    });
    run.value = created;
    progress.value =
      created.status === "queued"
        ? "任务已排队，等待规划 Worker…"
        : "规划 Agent 正在处理旅行需求…";
    await poll(created.id);
  } catch (cause) {
    error.value = messageOf(cause);
    busy.value = false;
  }
}

async function poll(runId: string) {
  if (!session.value || !active) return;
  try {
    const events = await getRunEvents(
      session.value.token,
      runId,
      sequence.value,
    );
    for (const event of events) {
      sequence.value = Math.max(sequence.value, event.sequence || 0);
      const payload = event.payload;
      if (
        payload.kind === "planning_run.progress" &&
        typeof payload.label === "string"
      )
        progress.value = payload.label;
      if (payload.kind === "planning.itinerary_created")
        progress.value = "行程已生成，正在读取经过校验的行程数据…";
    }
    const latest = await getRun(session.value.token, runId);
    run.value = latest;
    if (latest.status === "queued")
      progress.value = "任务已排队，等待规划 Worker…";
    if (
      latest.status === "running" &&
      !events.length &&
      !progress.value.includes("高德")
    )
      progress.value = "Agent 正在检索地点并优化逐日安排…";
    if (latest.status === "waiting_user")
      throw new ApiError(
        409,
        "当前规划需要补充信息；请改用完整对话工作区继续。",
      );
    if (latest.status === "failed")
      throw new ApiError(
        500,
        latest.error_public?.message ||
          "规划任务失败，请检查模型和高德服务配置。",
      );
    if (latest.status === "cancelled")
      throw new ApiError(409, "规划任务已取消。");
    if (latest.status === "succeeded") {
      if (!latest.result_itinerary_id)
        throw new ApiError(500, "任务完成但没有返回行程编号。");
      progress.value = "行程生成完成。";
      itinerary.value = await getItinerary(
        session.value.token,
        latest.result_itinerary_id,
      );
      busy.value = false;
      return;
    }
    pollTimer = setTimeout(() => void poll(runId), 1200);
  } catch (cause) {
    error.value = messageOf(cause);
    busy.value = false;
  }
}

function signOut() {
  if (pollTimer) clearTimeout(pollTimer);
  session.value = null;
  run.value = null;
  itinerary.value = null;
  error.value = "";
  busy.value = false;
}

function focusLabel(focus: TripFocus) {
  return focus === "sights_first"
    ? "景点为主"
    : focus === "food_first"
      ? "美食为主"
      : "均衡安排";
}
</script>

<template>
  <main class="page-shell">
    <header class="topbar">
      <a class="brand" href="#top" aria-label="途见首页"
        ><span class="brand-mark">途</span
        ><span>途见 <small>FLOATTRIP</small></span></a
      >
      <div class="topbar-right">
        <span class="api-status"
          ><i
            :class="{
              offline: apiHealthy === false,
              checking: apiHealthy === null,
            }"
          ></i
          >{{
            apiHealthy === true
              ? "规划服务在线"
              : apiHealthy === false
                ? "服务暂不可达"
                : "检查规划服务"
          }}</span
        ><button v-if="session" class="text-button" @click="signOut">
          退出登录
        </button>
      </div>
    </header>

    <section id="top" class="hero">
      <p class="eyebrow">LANGGRAPH TRAVEL PLANNER</p>
      <h1>把旅行想法，<span>排成真实行程。</span></h1>
      <p class="hero-copy">
        描述日期与偏好，Agent 查询真实地点，再由约束求解器安排每天的路线。
      </p>
      <div class="flow">
        <span>需求确认</span><b>→</b><span>高德 POI</span><b>→</b
        ><span>LangGraph</span><b>→</b><span>路线求解</span>
      </div>
    </section>

    <section class="workspace">
      <div class="form-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">YOUR NEXT TRIP</p>
            <h2>规划条件</h2>
          </div>
          <span class="step-count">01 / 02</span>
        </div>

        <form v-if="!session" class="auth-form" @submit.prevent="submitAuth">
          <div class="auth-tabs">
            <button
              type="button"
              :class="{ active: authMode === 'register' }"
              @click="authMode = 'register'"
            >
              创建演示账号</button
            ><button
              type="button"
              :class="{ active: authMode === 'login' }"
              @click="authMode = 'login'"
            >
              已有账号登录
            </button>
          </div>
          <label
            >用户名<input
              v-model="credentials.username"
              required
              minlength="1"
              maxlength="50"
              autocomplete="username"
              placeholder="输入用户名"
          /></label>
          <label
            >密码<input
              v-model="credentials.password"
              required
              type="password"
              minlength="1"
              autocomplete="current-password"
              placeholder="输入密码"
          /></label>
          <p class="helper">
            账号由当前 FastAPI 服务保存，演示客户端不会把凭据写入浏览器存储。
          </p>
          <button class="primary-button" type="submit">
            {{ authMode === "register" ? "注册并继续" : "登录并继续" }}
            <span>↗</span>
          </button>
        </form>

        <form v-else class="planning-form" @submit.prevent="startPlanning">
          <div class="signed-in">
            以 <strong>{{ session.username }}</strong> 身份规划
          </div>
          <label
            >目的地<input
              v-model="form.destination"
              required
              maxlength="40"
              placeholder="例如：南京"
          /></label>
          <div class="field-row">
            <label
              >出发日期<input v-model="form.startDate" required type="date"
            /></label>
            <label
              >旅行天数<select v-model.number="form.days">
                <option v-for="day in 7" :key="day" :value="day">
                  {{ day }} 天
                </option>
              </select></label
            >
          </div>
          <div class="date-note">
            结束日期 <strong>{{ endDate || "选择出发日期后自动计算" }}</strong>
          </div>
          <label
            >旅行侧重点<select v-model="form.focus">
              <option value="sights_first">景点为主</option>
              <option value="food_first">美食为主</option>
              <option value="balanced">均衡安排</option>
            </select></label
          >
          <label
            >景点偏好<input
              v-model="form.attractions"
              maxlength="160"
              placeholder="历史古迹、博物馆"
          /></label>
          <label
            >餐饮偏好 <span class="optional">选填</span
            ><input
              v-model="form.food"
              maxlength="120"
              placeholder="本地小吃、清淡口味"
          /></label>
          <label
            >节奏与约束<input
              v-model="form.pace"
              maxlength="160"
              placeholder="每天不超过 3 个景点，留出休息时间"
          /></label>
          <button
            class="primary-button"
            type="submit"
            :disabled="busy || !form.startDate"
          >
            {{ busy ? "正在规划…" : "生成逐日行程" }} <span>↗</span>
          </button>
          <p class="helper">
            请求由后端 Pydantic 校验，并进入持久化规划 Runtime。
          </p>
        </form>
      </div>

      <aside class="result-panel" aria-live="polite">
        <div class="panel-heading result-heading">
          <div>
            <p class="eyebrow">AGENT WORKSPACE</p>
            <h2>{{ itinerary ? "你的旅行安排" : "规划过程" }}</h2>
          </div>
          <span class="live-badge"
            ><i></i
            >{{ itinerary ? "已完成" : busy ? "进行中" : "待启动" }}</span
          >
        </div>

        <div v-if="!itinerary" class="progress-state">
          <div class="route-illustration" :class="{ spinning: busy }">
            <span class="route-line"></span
            ><span class="route-pin pin-one">A</span
            ><span class="route-pin pin-two">B</span
            ><span class="route-pin pin-three">✦</span>
          </div>
          <h3>{{ busy ? "正在为你整理路线" : "行程会显示在这里" }}</h3>
          <p>{{ progress }}</p>
          <div class="pipeline">
            <div
              :class="{
                complete: !!run,
                active: busy && run?.status === 'queued',
              }"
            >
              <b>1</b><span>创建任务</span>
            </div>
            <i></i>
            <div
              :class="{
                active: busy && run?.status === 'running',
                complete: !!itinerary,
              }"
            >
              <b>2</b><span>Agent 规划</span>
            </div>
            <i></i>
            <div :class="{ complete: !!itinerary }">
              <b>3</b><span>读取结果</span>
            </div>
          </div>
        </div>

        <div v-else class="itinerary-result">
          <div class="trip-summary">
            <span class="destination-icon">⌖</span>
            <div>
              <h3>
                {{ itinerary.plan.destination || form.destination }} ·
                {{
                  itinerary.plan.days_count || itinerary.plan.days.length
                }}
                日游
              </h3>
              <p>
                {{ itinerary.plan.start_date }} — {{ itinerary.plan.end_date }}
                <span>· {{ focusLabel(form.focus) }}</span>
              </p>
            </div>
          </div>
          <div v-if="itinerary.plan.solver_diagnostics" class="solver-strip">
            <span
              ><i></i> CP-SAT
              {{ itinerary.plan.solver_diagnostics.status || "已运行" }}</span
            ><span
              >{{
                itinerary.plan.solver_diagnostics.elapsed_ms ?? "—"
              }}
              ms</span
            >
          </div>
          <div class="day-list">
            <article
              v-for="day in itinerary.plan.days"
              :key="day.day"
              class="day-card"
            >
              <div class="day-header">
                <span class="day-index"
                  >D{{ String(day.day).padStart(2, "0") }}</span
                >
                <div>
                  <strong>{{ day.theme || `第 ${day.day} 天` }}</strong
                  ><small>{{ day.date || `旅行第 ${day.day} 天` }}</small>
                </div>
                <span class="stop-count"
                  >{{ day.timeline?.length || 0 }} 站</span
                >
              </div>
              <div class="timeline">
                <div
                  v-for="(item, index) in day.timeline"
                  :key="`${item.name}-${index}`"
                  class="stop"
                >
                  <div class="stop-rail">
                    <span
                      :class="
                        item.type === 'attraction'
                          ? 'attraction-dot'
                          : 'food-dot'
                      "
                    ></span
                    ><i v-if="index < day.timeline.length - 1"></i>
                  </div>
                  <div class="stop-copy">
                    <div class="stop-title">
                      <strong>{{ item.name || "行程安排" }}</strong
                      ><time
                        >{{ item.start_time || "--:--"
                        }}<template v-if="item.end_time">
                          — {{ item.end_time }}</template
                        ></time
                      >
                    </div>
                    <p>
                      {{
                        item.address ||
                        item.tip ||
                        (item.type === "attraction"
                          ? "景点安排"
                          : item.type === "lunch"
                            ? "午餐"
                            : item.type === "dinner"
                              ? "晚餐"
                              : "途中安排")
                      }}<span v-if="item.dist_from_prev_km != null">
                        · {{ item.dist_from_prev_km }} km</span
                      >
                    </p>
                  </div>
                </div>
              </div>
            </article>
          </div>
          <div
            v-if="
              itinerary.plan.route_issues?.length || itinerary.plan.weather_note
            "
            class="notice"
          >
            <strong>出行提示</strong>
            <p v-if="itinerary.plan.weather_note">
              {{ itinerary.plan.weather_note }}
            </p>
            <p v-for="issue in itinerary.plan.route_issues" :key="issue">
              {{ issue }}
            </p>
          </div>
          <p class="source-note">
            地点数据由后端服务查询；开放、预约和交通信息请出发前再次确认。
          </p>
        </div>

        <div v-if="error" class="error-card" role="alert">
          <span>!</span>
          <div>
            <strong>这次请求没有完成</strong>
            <p>{{ error }}</p>
            <button
              v-if="session && !busy"
              class="retry-button"
              @click="startPlanning"
            >
              重试规划
            </button>
          </div>
        </div>
      </aside>
    </section>

    <footer>
      <span>途见 · FloatTrip</span
      ><span>Vue 3 + TypeScript · FastAPI · LangGraph</span>
    </footer>
  </main>
</template>
