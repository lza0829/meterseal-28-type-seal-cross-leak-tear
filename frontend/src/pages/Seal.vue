<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 表类型, 写闸状态, 批状态, 标 } from '../labels'

const periods = ref([])
const periodId = ref('2026-10')
const meterType = ref('water')
const batchId = ref('')
const batches = ref([])
const msg = ref('')
const err = ref('')

async function refresh() {
  periods.value = await api.periods()
  batches.value = await api.batches(periodId.value, meterType.value)
  batchId.value = batches.value.find(b => b.status === 'open')?.batch_id || ''
}

async function seal() {
  err.value = ''; msg.value = ''
  if (!meterType.value) {
    err.value = '封印必须选择表类型，禁止未带类型封印'
    return
  }
  try {
    const r = await api.seal({
      period_id: periodId.value,
      meter_type: meterType.value,
      batch_id: batchId.value || null,
    })
    msg.value = `已封印 ${r.period_id} 的${标(表类型, r.meter_type)}，绑定批 ${r.batch_id}；另一表类型未封印仍可写`
    await refresh()
  } catch (e) { err.value = e.message }
}

onMounted(refresh)

</script>
<template>
  <div>
    <h1>分类型封印</h1>
    <p class="sub">按表类型分桶封印：只关闭所选类型的写闸，水表封了电表仍可写实抄，反之亦然</p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="refresh" /></label>
      <label>表类型（必选）
        <select v-model="meterType" @change="refresh">
          <option value="water">水表</option>
          <option value="electric">电表</option>
        </select>
      </label>
      <label>{{ 标(表类型, meterType) }}对账批
        <select v-model="batchId">
          <option value="">最新开放批（自动取）</option>
          <option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">
            {{ b.batch_id }} · {{ 标(批状态, b.status) }}
          </option>
        </select>
      </label>
      <button class="warn" @click="seal">封印{{ 标(表类型, meterType) }}</button>
      <button class="secondary" @click="refresh">刷新</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>

    <div class="panel">
      <h3>各账期水/电写闸（与写入拦截同口径）</h3>
      <table>
        <thead>
          <tr><th>账期</th><th>水表写闸</th><th>水表封印时刻</th><th>水表绑定批</th>
              <th>电表写闸</th><th>电表封印时刻</th><th>电表绑定批</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in periods" :key="p.period_id">
            <td>{{ p.period_id }}</td>
            <template v-for="t in ['water','electric']" :key="t">
              <td>
                <span class="tag" :class="(p.gates.find(g => g.meter_type === t) || {}).status">
                  {{ 标(写闸状态, (p.gates.find(g => g.meter_type === t) || {}).status) }}
                </span>
              </td>
              <td>{{ (p.gates.find(g => g.meter_type === t) || {}).sealed_at || '—' }}</td>
              <td>{{ (p.gates.find(g => g.meter_type === t) || {}).batch_no || '—' }}</td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>{{ periodId }} · {{ 标(表类型, meterType) }}类型批列表</h3>
      <table>
        <thead><tr><th>批号</th><th>类型</th><th>匹配</th><th>差额</th><th>状态</th><th>建立时刻</th></tr></thead>
        <tbody>
          <tr v-for="b in batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ 标(表类型, b.meter_type) }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
            <td>{{ b.created_at }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="6">暂无该类型批</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
