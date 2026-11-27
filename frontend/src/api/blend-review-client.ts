/** 试配审评 API：拼配小样领取、评审签结/更正、配方确认回执、审评视图。 */
import { fetchJson, request } from './client'

const API = '/api/blend-review'

export interface Signoff {
  id: number
  sample_id: number
  blend_id: number
  version: number
  taste_score: number
  comment: string
  aroma: string
  reviewer: string
  is_correction: boolean
  supersedes_signoff_id: number | null
  created_at_text: string
}

export interface BlendSample {
  id: number
  sample_code: string
  blend_id: number
  留样编号: string
  样品形态: string
  负责人: string
  领取日期: string
  备注说明: string
  review_status: string
  current_signoff_id: number | null
  current_signoff: Signoff | null
  signoff_history: Signoff[]
  signoff_count: number
  review_completed: boolean
}

export interface ConfirmReceipt {
  receipt_no: string
  blend_id: number
  sample_id: number
  sample_code: string
  signoff_id: number
  signoff_version: number
  signoff_snapshot: Signoff
  taste_score: number
  comment: string
  operator: string
  handled_at_text: string
  status: string
}

export interface FormulaReviewView {
  found: boolean
  blend_id: number
  blend: Record<string, string | number | boolean>
  formula_status: string
  samples: BlendSample[]
  eligible_sample_ids: number[]
  receipts: ConfirmReceipt[]
}

export interface QueueItem {
  blend_id: number
  登记编号: string
  原料比例: string
  拼配日期: string
  status: string
  samples: BlendSample[]
  receipts: ConfirmReceipt[]
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload?.detail ?? '请求失败')
  }
  return payload as T
}

export async function fetchReviewQueue(status?: string): Promise<QueueItem[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const data = await fetchJson<{ ok: boolean; items: QueueItem[] }>(`${API}/queue${query}`)
  return data.items
}

export async function fetchFormulaReview(blendId: number): Promise<FormulaReviewView> {
  const data = await fetchJson<{ ok: boolean; view: FormulaReviewView }>(
    `${API}/formulas/${blendId}`,
  )
  return data.view
}

export async function issueSample(values: Record<string, string | number>): Promise<unknown> {
  return postJson(`${API}/samples`, { values })
}

export async function submitSignoff(
  sampleId: number,
  payload: { taste_score: number; comment: string; aroma?: string; reviewer?: string },
): Promise<{ ok: boolean; message: string; signoff: Signoff }> {
  return postJson(`${API}/samples/${sampleId}/signoffs`, payload)
}

export async function confirmFormula(
  blendId: number,
  signoffId: number,
  requestId?: string,
): Promise<{ ok: boolean; message: string; receipt: ConfirmReceipt }> {
  return postJson(`${API}/formulas/${blendId}/confirm`, {
    signoff_id: signoffId,
    request_id: requestId,
  })
}
