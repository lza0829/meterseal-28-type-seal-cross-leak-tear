<template>
  <div class="frame">
    <header class="mast">
      <div class="seal-mark" aria-hidden="true">抄</div>
      <div class="mast-text">
        <h1 class="product">抄表簿</h1>
        <p class="tagline">估抄入湖 · 实抄对照 · 账期落封</p>
      </div>
      <nav class="tabs">
        <router-link to="/">总览</router-link>
        <router-link to="/meters">表具</router-link>
        <router-link to="/estimates">估抄</router-link>
        <router-link to="/actuals">实抄</router-link>
        <router-link to="/reconcile">对齐差额</router-link>
        <router-link to="/seal">账期封印</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
    </header>
    <main class="work">
      <router-view />
    </main>
  </div>
</template>
