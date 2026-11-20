<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
const current = ref('')
const msg = ref('')
const err = ref('')
async function load() {
  const s = await api.settings()
  current.value = s.current_period || ''
}
async function save() {
  err.value = ''; msg.value = ''
  try {
    await api.putSetting('current_period', current.value)
    msg.value = '已保存当前账期'
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <h1>设置</h1>
    <p class="sub">运行参数</p>
    <div class="panel">
      <div class="row">
        <label>当前账期<input v-model="current" /></label>
        <button @click="save">保存</button>
      </div>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
    </div>
  </div>
</template>
