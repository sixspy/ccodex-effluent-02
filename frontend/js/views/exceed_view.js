export const exceedViewTemplate = `
  <div class="panel">
    <h3>超标旁路挂起</h3>
    <div v-if="exceeds.filter(h => h.status==='开放' || h.status==='开放挂起').length"
         class="banner err" data-exceed-open-banner>
      开放挂起列表非空：超标未清禁准排（effluent-02 已封取舍·甲路径，当日封存）。
      对应批次准排页提交键置灰、准排写口拒写；对账不得计为已准排。
    </div>
    <table>
      <thead><tr><th>键</th><th>采样</th><th>指标</th><th>测值</th><th>限值</th><th>状态</th><th>禁准排</th><th></th></tr></thead>
      <tbody>
        <tr v-for="h in exceeds" :key="h.id">
          <td>{{ h.hold_key }}</td><td>{{ h.sample_code }}</td><td>{{ h.exceed_metric }}</td>
          <td>{{ h.measured }}</td><td>{{ h.limit_value }}</td>
          <td :class="(h.status==='开放' || h.status==='开放挂起')?'fail':'pass'">{{ h.status }}</td>
          <td>
            <span v-if="h.status==='开放' || h.status==='开放挂起'" class="fail" data-exceed-forbid-badge>⛔ 禁准排</span>
            <span v-else class="pass">已闭环</span>
          </td>
          <td><button v-if="h.status==='开放' || h.status==='开放挂起'" class="secondary" style="width:auto;margin:0" @click="closeExceed(h.id)">闭环</button></td>
        </tr>
      </tbody>
    </table>
  </div>`;
