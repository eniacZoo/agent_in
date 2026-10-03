const { createApp } = Vue;

createApp({
  data() {
    return {
      q: "", category: "", sort: "id", desc: 0, page: 1, size: 20,
      total: 0, categories: [], items: [],
      current: null, form: null, dirty: false, chart: null,
    };
  },
  mounted() { this.load(); this.loadStats(); },
  methods: {
    async load() {
      const p = new URLSearchParams({ q: this.q, category: this.category, sort: this.sort, desc: this.desc, page: this.page, size: this.size });
      const data = await (await fetch("/api/records?" + p)).json();
      this.items = data.items; this.total = data.total; this.categories = data.categories;
    },
    async loadStats() {
      const data = await (await fetch("/api/stats")).json();
      const el = document.getElementById("chart");
      this.chart = this.chart || echarts.init(el);
      this.chart.setOption({
        grid: { left: 40, right: 12, top: 16, bottom: 28 },
        xAxis: { type: "category", data: data.by_category.map(d => d.category || "未分类") },
        yAxis: { type: "value" },
        series: [{ type: "bar", data: data.by_category.map(d => d.total), itemStyle: { color: "#9c2f2f" } }],
      });
    },
    async open(row) {
      if (this.dirty && !confirm("有未保存的修改，离开？")) return;
      const data = await (await fetch("/api/records/" + row.id)).json();
      this.current = data;
      this.form = { name: data.record.name, category: data.record.category, amount: data.record.amount, note: data.record.note };
      this.dirty = false;
    },
    setSort(key) {
      if (this.sort === key) this.desc = this.desc ? 0 : 1; else { this.sort = key; this.desc = 0; }
      this.load();
    },
    async save() {
      const res = await fetch("/api/records/" + this.current.record.id, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(this.form),
      });
      if (!res.ok) { alert("保存失败"); return; }
      this.current = await res.json();
      this.dirty = false;
      this.load(); this.loadStats();
    },
  },
  template: `
  <header><h1>数据</h1><a href="/api/export">导出 CSV</a></header>
  <div class="filters">
    <input v-model="q" placeholder="搜索" @keyup.enter="page=1; load()" />
    <select v-model="category" @change="page=1; load()">
      <option value="">全部分类</option>
      <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
    </select>
    <button @click="page=1; load()">查询</button>
  </div>
  <div class="layout">
    <div class="card">
      <table>
        <thead><tr>
          <th @click="setSort('name')">名称</th>
          <th @click="setSort('category')">分类</th>
          <th class="num" @click="setSort('amount')">数值</th>
        </tr></thead>
        <tbody>
          <tr v-for="row in items" :key="row.id" :class="{active: current && current.record.id===row.id}" @click="open(row)">
            <td>{{ row.name }}</td><td>{{ row.category }}</td><td class="num">{{ row.amount }}</td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <span>{{ total }} 条</span>
        <button @click="page=Math.max(1,page-1); load()">上一页</button>
        <span>{{ page }}</span>
        <button @click="page=page+1; load()">下一页</button>
      </div>
    </div>
    <div>
      <div class="card" id="chart"></div>
      <div class="card" v-if="current" style="margin-top:16px">
        <label>名称<input v-model="form.name" @input="dirty=true" /></label>
        <label>分类<input v-model="form.category" @input="dirty=true" /></label>
        <label>数值<input type="number" v-model.number="form.amount" @input="dirty=true" /></label>
        <label>备注<input v-model="form.note" @input="dirty=true" /></label>
        <button class="primary" @click="save">保存</button>
        <p class="raw">{{ current.record.raw_text }}</p>
        <p class="log" v-for="log in current.logs" :key="log.id">{{ log.field }}：{{ log.old_value }} → {{ log.new_value }}</p>
      </div>
    </div>
  </div>`,
}).mount("#app");
