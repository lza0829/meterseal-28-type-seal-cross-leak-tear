<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 表类型, 标 } from '../labels'
const periodId = ref('2026-10')
const meterType = ref('')
const rows = ref([])
const form = ref({ meter_id: 'M-W-101', period_id: '2026-10', qty: 10, doc_no: '' })
const msg = ref('')
const err = ref('')
async function load() {
  err.value = ''; msg.value = ''
  rows.value = await api.estimates(periodId.value, meterType.value || undefined)
}
async function submit() {
  err.value = ''; msg.value = ''
  try {
    const body = { ...form.value, doc_no: form.value.doc_no || ('估-' + Date.now()) }
    await api.postEstimate(body)
    msg.value = '估抄已装载（若该表类型账期已封印会被拒写）'
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <h1>估抄</h1>
    <p class="sub">按账期装载估抄读数；封印按表类型分闸，只拦截已封印类型</p>
    <div class="row">
      <label>账期筛选<input v-model="periodId" /></label>
      <label>表类型
        <select v-model="meterType" @change="load">
          <option value="">全部</option>
          <option value="water">水表</option>
          <option value="electric">电表</option>
        </select>
      </label>
      <button class="secondary" @click="load">刷新</button>
    </div>
    <div class="panel">
      <div class="row">
        <label>表号<input v-model="form.meter_id" /></label>
        <label>账期<input v-model="form.period_id" /></label>
        <label>数量<input type="number" step="0.1" v-model.number="form.qty" /></label>
        <label>单号<input v-model="form.doc_no" placeholder="可空自动生成" /></label>
        <button @click="submit">装载估抄</button>
      </div>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
      <table>
        <thead><tr><th>编号</th><th>表号</th><th>类型</th><th>账期</th><th>数量</th><th>单号</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.estimate_id">
            <td>{{ r.estimate_id }}</td>
            <td>{{ r.meter_id }}</td>
            <td>{{ 标(表类型, r.meter_type) }}</td>
            <td>{{ r.period_id }}</td>
            <td>{{ r.qty }}</td>
            <td>{{ r.doc_no }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
