<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 表类型, 表状态, 标 } from '../labels'
const rows = ref([])
const err = ref('')
onMounted(async () => {
  try { rows.value = await api.meters() } catch (e) { err.value = e.message }
})
</script>
<template>
  <div>
    <h1>表具</h1>
    <p class="sub">园区表具：楼栋、户号、水表电表</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>表号</th><th>楼栋</th><th>户号</th><th>类型</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="m in rows" :key="m.meter_id">
            <td>{{ m.meter_id }}</td>
            <td>{{ m.building }}</td>
            <td>{{ m.unit_label }}</td>
            <td>{{ 标(表类型, m.meter_type) }}</td>
            <td>{{ 标(表状态, m.status) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
