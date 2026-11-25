export const reconcileViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>许可对账</h3>
      <div class="actions"><button class="secondary" @click="loadReconcile">刷新全部</button></div>
      <table>
        <thead><tr><th>许可</th><th>对齐</th><th>证明</th><th>封批数</th><th>有效准排</th><th>开放超标</th><th>齐套灯</th></tr></thead>
        <tbody>
          <tr v-for="r in reconAllItems" :key="r.permit_code">
            <td>{{ r.permit_code }}</td>
            <td :class="r.balanced?'pass':'fail'">{{ r.balanced ? "齐" : "不齐" }}</td>
            <td :class="r.proof_ready?'pass':'warn'">{{ r.proof_ready ? "可证" : "未齐" }}</td>
            <td>{{ r.seal_count || 0 }}</td>
            <td :class="r.release_blocked_count ? 'warn' : 'pass'" data-recon-released>{{ r.released_count || 0 }}</td>
            <td :class="r.open_hold_count ? 'fail' : 'pass'" data-recon-open>{{ r.open_hold_count || 0 }}</td>
            <td :class="r.complete_lamp === '绿' ? 'pass' : 'fail'" data-recon-lamp>
              {{ r.complete_lamp || (r.release_complete ? "绿" : "红") }}
            </td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="font-size:0.8rem">
        口径：有效准排 = 正式准排册有“已准排”行且该批无开放超标；影子准排意图未原子升正前不计已准排。
      </p>
    </div>
    <div class="panel">
      <h3>单许可 / 采样证明</h3>
      <label>许可号</label><input v-model="reconCode"/>
      <div class="actions"><button @click="loadReconcileOne">对账</button></div>
      <pre v-if="reconOne" style="white-space:pre-wrap;font-size:0.82rem">{{ JSON.stringify(reconOne, null, 2) }}</pre>
      <label>采样号证明</label><input v-model="proofCode"/>
      <div class="actions"><button class="secondary" @click="loadProof">证明</button></div>
      <pre v-if="proofOne" style="white-space:pre-wrap;font-size:0.82rem">{{ JSON.stringify(proofOne, null, 2) }}</pre>
    </div>
  </div>`;
