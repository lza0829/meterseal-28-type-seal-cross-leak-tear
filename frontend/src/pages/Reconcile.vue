<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 命运, 表类型, 标 } from '../labels'

const periodId = ref('2026-10')
const meterType = ref('water')
const batch = ref(null)
const vars = ref([])
const msg = ref('')
const err = ref('')

async function loadBatch() {
  const bs = await api.batches(periodId.value, meterType.value)
  if (bs[0]) {
    batch.value = bs[0]
    vars.value = await api.variances({ batch_id: bs[0].batch_id })
  } else {
    batch.value = null
    vars.value = []
  }
}

async function align() {
  err.value = ''; msg.value = ''
  try {
    batch.value = await api.align(periodId.value, meterType.value)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
    msg.value = `对齐完成（${标(表类型, meterType.value)}）：匹配 ${batch.value.matched_count}，差额 ${batch.value.variance_count}`
  } catch (e) { err.value = e.message }
}

async function fate(id, f) {
  err.value = ''
  try {
    await api.setFate(id, f)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
  } catch (e) { err.value = e.message }
}

onMounted(loadBatch)
</script>
<template>
  <div>
    <h1>对齐差额</h1>
    <p class="sub">按表类型分桶对齐：水表、电表各自成批，互不混用</p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="loadBatch" /></label>
      <label>表类型
        <select v-model="meterType" @change="loadBatch">
          <option value="water">水表</option>
          <option value="electric">电表</option>
        </select>
      </label>
      <button @click="align">跑{{ 标(表类型, meterType) }}对齐</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <div class="panel" v-if="batch">
      <p>
        批号 {{ batch.batch_id }} · 类型 {{ 标(表类型, batch.meter_type) }} · 指纹 {{ batch.fingerprint }} ·
        <span class="tag" :class="batch.status">{{ 标(批状态, batch.status) }}</span>
      </p>
      <table>
        <thead><tr><th>差额编号</th><th>表号</th><th>类型</th><th>估抄</th><th>实抄</th><th>差值</th><th>命运</th><th>处置</th></tr></thead>
        <tbody>
          <tr v-for="v in vars" :key="v.variance_id">
            <td>{{ v.variance_id }}</td>
            <td>{{ v.meter_id }}</td>
            <td>{{ 标(表类型, v.meter_type) }}</td>
            <td>{{ v.estimate_qty }}</td>
            <td>{{ v.actual_qty }}</td>
            <td>{{ v.delta }}</td>
            <td>{{ 标(命运, v.fate) }}</td>
            <td>
              <button class="secondary" @click="fate(v.variance_id, 'accept_short')">认短</button>
              <button class="secondary" @click="fate(v.variance_id, 'accept_over')">认溢</button>
              <button class="warn" @click="fate(v.variance_id, 'quarantine')">隔离</button>
            </td>
          </tr>
          <tr v-if="!vars.length"><td colspan="8">本类型批无差额（全部匹配）</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-else>
      <p class="msg">{{ periodId }} 账期下暂无{{ 标(表类型, meterType) }}对账批，请先对齐</p>
    </div>
  </div>
</template>
