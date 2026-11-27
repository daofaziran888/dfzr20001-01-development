<template>
  <section class="page review-page">
    <header class="page-head">
      <div>
        <h2>试配审评</h2>
        <p class="page-desc">
          制茶师从留样结果并排查看各拼配小样的评审签结，凭已完成审评且实际关联本配方的小样确认配方；
          评语只登记在当初领取的小样上，分数更正全程留痕。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="showIssue = true">领取小样</button>
        <button class="btn ghost" type="button" @click="reload">刷新队列</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">配方总数</span>
        <strong class="stat-value">{{ queue.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待确认配方</span>
        <strong class="stat-value">{{ pendingCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已确认配方</span>
        <strong class="stat-value">{{ confirmedCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已办理回执</span>
        <strong class="stat-value">{{ totalReceipts }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>配方状态</span>
        <select v-model="statusFilter" data-testid="filter-status" @change="reload">
          <option value="">全部</option>
          <option value="草稿">草稿</option>
          <option value="待确认">待确认</option>
          <option value="已确认">已确认</option>
          <option value="已撤回">已撤回</option>
        </select>
      </label>
    </form>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="infoMessage" class="info-text">{{ infoMessage }}</p>

    <div v-for="item in queue" :key="item.blend_id" class="formula-card" :data-testid="`formula-${item.blend_id}`">
      <div class="formula-head">
        <div>
          <strong>配方 #{{ item.blend_id }}（{{ item['登记编号'] || '—' }}）</strong>
          <span class="status-tag" :class="item.status">{{ item.status }}</span>
        </div>
        <div class="formula-meta">
          <span>原料比例：{{ item['原料比例'] || '—' }}</span>
          <span>拼配日期：{{ item['拼配日期'] || '—' }}</span>
          <span>小样数：{{ item.samples.length }}</span>
        </div>
      </div>

      <div v-if="!item.samples.length" class="empty-inline">该配方尚无领取的拼配小样</div>

      <div class="sample-grid">
        <article
          v-for="sample in item.samples"
          :key="sample.id"
          class="sample-card"
          :data-testid="`sample-${sample.id}`"
          :class="{ eligible: isEligible(item, sample) }"
        >
          <header class="sample-head">
            <strong>{{ sample.sample_code }}</strong>
            <span class="status-tag" :class="sample.review_status">{{ sample.review_status }}</span>
          </header>
          <p class="sample-line">留样编号：{{ sample['留样编号'] }}</p>
          <p class="sample-line">负责人：{{ sample['负责人'] || '—' }}　领取：{{ sample['领取日期'] || '—' }}</p>

          <div class="score-box">
            <span class="score-label">当前口感评分</span>
            <strong class="score-value">
              {{ sample.current_signoff ? sample.current_signoff.taste_score : '—' }}
            </strong>
            <span v-if="sample.signoff_count > 1" class="corrected-badge">
              已更正 {{ sample.signoff_count - 1 }} 次
            </span>
          </div>

          <!-- 历次评分并排呈现，更正前分数不被覆盖 -->
          <table v-if="sample.signoff_history.length" class="signoff-table">
            <thead>
              <tr><th>版本</th><th>分数</th><th>评语</th><th>审评员</th><th>时间</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="signoff in sample.signoff_history"
                :key="signoff.id"
                :data-testid="`signoff-${signoff.id}`"
                :class="{ superseded: signoff.is_correction === false && sample.signoff_count > 1 }"
              >
                <td>
                  第{{ signoff.version }}版
                  <span v-if="signoff.is_correction" class="mini-badge">更正</span>
                </td>
                <td>{{ signoff.taste_score }}</td>
                <td class="comment-cell">{{ signoff.comment }}</td>
                <td>{{ signoff.reviewer }}</td>
                <td>{{ signoff.created_at_text }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-inline">尚未评审签结</p>

          <footer class="sample-actions">
            <button
              class="btn small"
              type="button"
              :data-testid="`open-signoff-${sample.id}`"
              @click="openSignoff(sample)"
            >
              {{ sample.signoff_history.length ? '更正评分/补写评语' : '登记评审签结' }}
            </button>
            <button
              v-if="isEligible(item, sample) && item.status !== '已确认'"
              class="btn primary small"
              type="button"
              :data-testid="`confirm-${sample.id}`"
              @click="onConfirm(item, sample)"
            >
              凭此签结确认配方
            </button>
            <span v-if="!sample.review_completed" class="hint-text">未完成审评，不能作为确认依据</span>
          </footer>
        </article>
      </div>

      <!-- 实际办件：已确认配方可查当初签结快照回执 -->
      <div v-if="item.receipts.length" class="receipt-box" :data-testid="`receipts-${item.blend_id}`">
        <h4>确认回执（实际办件）</h4>
        <div v-for="receipt in item.receipts" :key="receipt.receipt_no" class="receipt-item">
          <div>
            <strong>{{ receipt.receipt_no }}</strong>
            <span class="status-tag 已办理">{{ receipt.status }}</span>
          </div>
          <p class="sample-line">
            依据小样：{{ receipt.sample_code }} ｜ 签结第{{ receipt.signoff_version }}版 ｜
            当时评分：<strong>{{ receipt.taste_score }}</strong> ｜ 办理人：{{ receipt.operator }} ｜
            时间：{{ receipt.handled_at_text }}
          </p>
          <p class="sample-line">回执快照评语：{{ receipt.comment }}</p>
        </div>
      </div>
    </div>

    <p v-if="!queue.length && !loading" class="empty-state">暂无配方数据</p>

    <!-- 领取小样弹窗 -->
    <div v-if="showIssue" class="modal-backdrop" data-testid="issue-modal">
      <form class="modal" @submit.prevent="submitIssue">
        <h3>领取拼配小样</h3>
        <p class="hint-text">小样从领取起即绑定唯一一张配方，评语只能登记在这只小样上。</p>
        <label class="create-field">
          <span>关联配方 ID（blend_id）</span>
          <input v-model.number="issueForm.blend_id" data-testid="issue-blend" type="number" required />
        </label>
        <label class="create-field">
          <span>小样编号</span>
          <input v-model="issueForm.sample_code" data-testid="issue-code" required />
        </label>
        <label class="create-field">
          <span>负责人</span>
          <input v-model="issueForm.负责人" data-testid="issue-owner" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showIssue = false">取消</button>
          <button class="btn primary" data-testid="issue-submit" type="submit">确认领取</button>
        </div>
      </form>
    </div>

    <!-- 评审签结 / 更正弹窗 -->
    <div v-if="signoffTarget" class="modal-backdrop" data-testid="signoff-modal">
      <form class="modal" @submit.prevent="submitSignoffForm">
        <h3>
          {{ signoffTarget.signoff_history.length ? '更正评审签结' : '登记评审签结' }}
          ：{{ signoffTarget.sample_code }}
        </h3>
        <p v-if="signoffTarget.signoff_history.length" class="hint-text">
          更正不会覆盖旧分数，系统会追加新版本并保留完整历史。
        </p>
        <label class="create-field">
          <span>口感评分（0-100）</span>
          <input v-model.number="signoffForm.taste_score" data-testid="signoff-score" type="number" min="0" max="100" required />
        </label>
        <label class="create-field">
          <span>试配评语</span>
          <input v-model="signoffForm.comment" data-testid="signoff-comment" required />
        </label>
        <label class="create-field">
          <span>香气描述</span>
          <input v-model="signoffForm.aroma" data-testid="signoff-aroma" />
        </label>
        <label class="create-field">
          <span>审评员</span>
          <input v-model="signoffForm.reviewer" data-testid="signoff-reviewer" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="signoffTarget = null">取消</button>
          <button class="btn primary" data-testid="signoff-submit" type="submit">提交签结</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  confirmFormula,
  fetchReviewQueue,
  issueSample,
  submitSignoff,
} from '@/api/blend-review-client'
import type { BlendSample, QueueItem } from '@/api/blend-review-client'

const queue = ref<QueueItem[]>([])
const loading = ref(false)
const errorMessage = ref('')
const infoMessage = ref('')
const statusFilter = ref('')

const showIssue = ref(false)
const issueForm = ref<{ blend_id: number | null; sample_code: string; 负责人: string }>({
  blend_id: null,
  sample_code: '',
  负责人: '',
})

const signoffTarget = ref<BlendSample | null>(null)
const signoffForm = ref({ taste_score: 80, comment: '', aroma: '', reviewer: '' })

const pendingCount = computed(
  () => queue.value.filter((item) => item.status === '待确认').length,
)
const confirmedCount = computed(
  () => queue.value.filter((item) => item.status === '已确认').length,
)
const totalReceipts = computed(() =>
  queue.value.reduce((sum, item) => sum + item.receipts.length, 0),
)

function isEligible(item: QueueItem, sample: BlendSample): boolean {
  // 已完成审评且实际关联该配方的小样才可作为确认依据
  return sample.review_completed && sample.blend_id === item.blend_id
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    queue.value = await fetchReviewQueue(statusFilter.value || undefined)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审评队列读取失败'
  } finally {
    loading.value = false
  }
}

async function submitIssue() {
  errorMessage.value = ''
  if (!issueForm.value.blend_id) {
    errorMessage.value = '请填写关联配方 ID'
    return
  }
  try {
    await issueSample({
      blend_id: issueForm.value.blend_id,
      sample_code: issueForm.value.sample_code,
      负责人: issueForm.value.负责人,
    })
    showIssue.value = false
    issueForm.value = { blend_id: null, sample_code: '', 负责人: '' }
    infoMessage.value = '小样已领取并绑定配方'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '小样领取失败'
  }
}

function openSignoff(sample: BlendSample) {
  signoffTarget.value = sample
  signoffForm.value = {
    taste_score: sample.current_signoff?.taste_score ?? 80,
    comment: '',
    aroma: '',
    reviewer: sample.current_signoff?.reviewer ?? '',
  }
}

async function submitSignoffForm() {
  if (!signoffTarget.value) return
  errorMessage.value = ''
  try {
    const result = await submitSignoff(signoffTarget.value.id, { ...signoffForm.value })
    signoffTarget.value = null
    infoMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '签结登记失败'
  }
}

async function onConfirm(item: QueueItem, sample: BlendSample) {
  errorMessage.value = ''
  if (!sample.current_signoff) {
    errorMessage.value = '该小样尚无评审签结'
    return
  }
  const signoffId = sample.current_signoff.id
  // 用签结 id 派生 request_id：同一签结重交天然幂等，不会多确认一份配方
  const requestId = `confirm-blend-${item.blend_id}-signoff-${signoffId}`
  try {
    const result = await confirmFormula(item.blend_id, signoffId, requestId)
    infoMessage.value = `${result.message}（回执 ${result.receipt.receipt_no}，依据小样 ${result.receipt.sample_code}）`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '配方确认失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.review-page { display: block; }
.info-text { color: #1f6feb; font-size: 13px; }
.formula-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.formula-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.formula-meta { display: flex; gap: 16px; color: var(--muted); font-size: 12px; }
.status-tag {
  display: inline-block;
  margin-left: 8px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
}
.status-tag.草稿 { background: #fff3cd; color: #856404; }
.status-tag.待确认 { background: #cce5ff; color: #004085; }
.status-tag.已确认, .status-tag.已办理 { background: #d4edda; color: #155724; }
.status-tag.已撤回 { background: #f8d7da; color: #721c24; }
.status-tag.已完成审评 { background: #d1fae5; color: #065f46; }
.status-tag.待审评 { background: #fef3c7; color: #92400e; }
.sample-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
}
.sample-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  background: #fafbfc;
}
.sample-card.eligible { border-color: #22c55e; box-shadow: 0 0 0 1px #22c55e inset; }
.sample-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.sample-line { font-size: 12px; color: var(--muted); margin: 2px 0; }
.score-box {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 8px;
  margin: 8px 0;
}
.score-label { font-size: 12px; color: var(--muted); }
.score-value { font-size: 22px; color: #b45309; }
.corrected-badge {
  margin-left: auto;
  font-size: 11px;
  color: #b42318;
  background: #fee2e2;
  padding: 2px 6px;
  border-radius: 8px;
}
.signoff-table { width: 100%; border-collapse: collapse; font-size: 11px; margin: 6px 0; }
.signoff-table th, .signoff-table td {
  border: 1px solid var(--border);
  padding: 4px 6px;
  text-align: left;
}
.signoff-table tr.superseded td { color: #94a3b8; text-decoration: line-through; }
.mini-badge {
  background: #fee2e2;
  color: #b42318;
  border-radius: 6px;
  padding: 0 4px;
  margin-left: 4px;
}
.comment-cell { max-width: 160px; }
.sample-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.btn.small { padding: 4px 10px; font-size: 12px; }
.hint-text { font-size: 12px; color: var(--muted); }
.empty-inline { color: var(--muted); font-size: 12px; padding: 8px 0; }
.receipt-box {
  margin-top: 12px;
  border-top: 2px solid #e5e7eb;
  padding-top: 10px;
  background: #f0fdf4;
  border-radius: 8px;
  padding: 10px 12px;
}
.receipt-box h4 { margin: 0 0 8px; font-size: 13px; color: #166534; }
.receipt-item { background: #fff; border: 1px solid #bbf7d0; border-radius: 6px; padding: 8px 10px; margin-bottom: 6px; }
</style>
