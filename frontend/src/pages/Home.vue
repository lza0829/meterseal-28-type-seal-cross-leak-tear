<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 表类型, 写闸状态, 标 } from '../labels'
const data = ref(null)
const err = ref('')
onMounted(async () => {
  try { data.value = await api.overview() } catch (e) { err.value = e.message }
})
</script>
<template>
  <div>
    <h1>总览</h1>
    <p class="sub">当前账期水/电分类型写闸、对齐与封印状态（计数与写闸同表同源）</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div class="grid" v-if="data">
      <div class="stat">当前账期<b>{{ data.current_period }}</b></div>
      <div class="stat">水表开放/已封印<b>{{ data.gate_counts.water.open }} / {{ data.gate_counts.water.sealed }}</b></div>
      <div class="stat">电表开放/已封印<b>{{ data.gate_counts.electric.open }} / {{ data.gate_counts.electric.sealed }}</b></div>
      <div class="stat">表具数（水/电）<b>{{ data.meter_count_by_type.water || 0 }} / {{ data.meter_count_by_type.electric || 0 }}</b></div>
      <div class="stat">未决差额（水/电）<b>{{ data.pending_by_type.water || 0 }} / {{ data.pending_by_type.electric || 0 }}</b></div>
    </div>
    <div class="panel" v-if="data">
      <h3>当前账期各类型写闸</h3>
      <table>
        <thead><tr><th>表类型</th><th>写闸状态</th><th>封印时刻</th><th>绑定批</th></tr></thead>
        <tbody>
          <tr v-for="g in data.current_gates" :key="g.meter_type">
            <td>{{ 标(表类型, g.meter_type) }}</td>
            <td><span class="tag" :class="g.status">{{ 标(写闸状态, g.status) }}</span></td>
            <td>{{ g.sealed_at || '—' }}</td>
            <td>{{ g.batch_no || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="data">
      <h3>最近对账批（类型批，不混桶）</h3>
      <table>
        <thead><tr><th>批号</th><th>账期</th><th>类型</th><th>匹配</th><th>差额</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in data.recent_batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ b.period_id }}</td>
            <td>{{ 标(表类型, b.meter_type) }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
