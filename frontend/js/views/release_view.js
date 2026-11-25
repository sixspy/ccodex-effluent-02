export const releaseViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>准排放记录（正式准排册）</h3>
      <table>
        <thead><tr><th>准排号</th><th>采样</th><th>口</th><th>值班</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td>{{ r.release_ref }}</td><td>{{ r.sample_code }}</td><td>{{ r.outfall_code }}</td>
            <td>{{ r.officer }}</td><td>{{ r.released_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>签发准排</h3>
      <div v-if="openHoldRows.length" class="banner err" data-open-hold-banner>
        超标页开放挂起 {{ openHoldRows.length }} 项未闭环：相关批次“超标未清禁准排”，选中后提交键置灰且写口拒写（effluent-02）。
      </div>
      <label>采样批次</label>
      <select v-model.number="releaseForm.sample_id" data-release-sample-select>
        <option v-for="s in releaseCandidates" :key="s.id" :value="s.id">
          {{ s.sample_code }} · {{ s.status }}{{ releaseBlocked(s.id) ? " · ⛔ 开放超标·禁准排" : "" }}
        </option>
      </select>
      <label>值班员</label><input v-model="releaseForm.officer"/>
      <p v-if="selectedReleaseBlocked" class="err" data-release-blocked-hint>
        该批仍有开放超标挂起，超标未清禁准排：提交键已置灰，后端写口同样拒写，正式准排册零增行。
      </p>
      <div class="actions">
        <button class="release-submit" data-release-submit
                :disabled="selectedReleaseBlocked || !releaseForm.sample_id"
                :class="{ 'btn-disabled': selectedReleaseBlocked }"
                @click="createRelease">
          {{ selectedReleaseBlocked ? "⛔ 超标未清·禁止准排" : "准排" }}
        </button>
      </div>
    </div>
  </div>`;
