# 619 单人首测 SOP

1. 在包根用已有 Python 3.11 和 `requirements.txt` 指定版本运行：

   ```console
   python -B offline_619.py --out offline_result
   ```

   保留offline_result及报错全文。它不连接真实API或机器人；失败先交滕飞定位，无需到场拼装分支。
2. 现场先核对实际版本、启动入口、技能终点、末端/相机适配、现场既有停止办法。只有已有可用真实入口时才按平台规程启动；缺Profile/Provider、权重、适配或停止条件时不试猜，填“未启动”。本包不提供硬件操作命令或新增资源授权。
3. 每次技能调用单独复制一份 `return_template.json`，按现场顺序命名，如 `01_抓碗.json`、`02_抓碗.json`。选择实际运行路径，填版本、入口和起止时间。把同次调用的脱敏原始请求（技能名、目标、实际参数）和原始返回分别列在 `original_call_request_files_redacted` 和 `original_log_and_return_files`。优先用已有的调用 ID 关联请求、返回和观测。同一任务两次抓碗会共用任务 ID。缺少调用 ID 时，附日志行号、时间和对应依据；证据不足写“无法关联”。
   - **Agent 派发**时，在 `agent_transition` 填 `applicability: "applicable"`。记录原指令与绑定目标、调用前状态、刚收到返回时的状态、实际选的下一步及依据。有 `scene_revision`、观测 ID 或 `resource_refs` 时附可打开的文件。收尾时在 `record_cutoff_time` 填本次记录截止时间，在 `task_state_at_record_cutoff` 填当时的任务状态，在 `record_cutoff_log_refs` 填日志文件和行号或时间。例如，超时后 Agent 决定“再观察一次”，截止时若仍在等待观测就填实际等待状态，只有日志记为完成才填完成。尚未决定就写“尚未决定”；缺少状态依据时填 `unknown`，在 `unknowns` 注明缺项。
   - **专家单测**：附专家入口实际收到的参数和返回，`agent_transition.applicability` 填 `not_applicable`，其余 Agent 字段留空。**未启动**：同样填 `not_applicable`，在 `not_started_reason_or_blocker` 写明缺项，请求和返回文件列表可留空。若 Agent 已经发出请求但启动失败，按 Agent 派发记录原始请求、错误和后续状态。
4. 动作前后各保留可辨碗和末端的观测，注明来源、时间和与调用的关联；现场已有RGB-D则保留原数据，无则如实写缺。记录人工干预、实际终态及unknown；抓起、放下、入盒分开描述，自动判据和人工判断分开。
5. 在 `expert_exit_reason` 填专家返回的原因，如 `time_budget` 耗尽，并保留原始返回、异常或退出码。在 `robot_actual_stop_state` 单独填现场确认的“已停”“仍在运动”或 `unknown`，在 `robot_stop_evidence_refs` 填控制器反馈、带时间的观测或现场人员确认记录的位置。仅见超时或停止指令时，实际停止状态填 `unknown`。物理效果另填 `actual_effect_and_how_confirmed`。整次未启动时退出及停止字段留空。
6. 首次结果可直接回传。如需排查后复测或验证复现，先按平台规程确认机器人已停、现场具备再次启动条件，再由钱玉航决定是否手动再测。每次复测保留单独记录，并注明相对上次改了什么（或“条件相同”）。停机状态未知时先交回现有记录，不自动重试。
7. 将各次 JSON、日志、观测交回滕飞，失败记录一并保留。陈复核入口、派发、返回与接续，李复核观测和判别范围。仅交本次所需材料，去除凭据、私聊、内部地址及无关个人文件。
