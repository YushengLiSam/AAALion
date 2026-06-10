# i18n — iOS 中英本地化工具链

让 app 的中英切换可复现:**改了/加了中文 UI 串后,一条命令重新生成 `.strings`。**

```bash
python tools/i18n/gen_strings.py
```

## 它做什么
1. 扫描 `client/AAALionApp/AAALionApp/**/*.swift` 里**所有含中文的字符串字面量**(忽略 `//` 注释、`LanguageManager.swift`)。
2. 对每个 key 求英文:
   - **双语 `"中文 / English"`** → 自动拆分(en 取英文半边,zh-Hans 取中文半边)。**优先用这种写法,零翻译成本。**
   - **纯中文 key** → 查 `translations.json`(中→英映射);查不到则在英文模式下**回退显示中文**(脚本会列出这些,提示你补翻译)。
3. 写出 `client/AAALionApp/AAALionApp/en.lproj/Localizable.strings`(英文)+ `zh-Hans.lproj/Localizable.strings`(把双语 key 剥成纯中文)。

## 运行时怎么生效(架构)
- `LanguageManager`(`@Observable`,`Views/.../Localization/LanguageManager.swift`)保存当前语言,运行时把 `Bundle.main` 指向对应 `.lproj`。
- SwiftUI 的 `Text("中文")` 把字面量当 `LocalizedStringKey` → 自动查 `.strings` → 切语言时**就地重渲染**(因为 `L()` 读的是 `@Observable` 的 bundle,无需 `.id()` 全树重建,不跳页)。
- **String 值 / 插值 / 三元** 不会自动本地化,要显式包:
  - 静态:`Text(L("中文"))`、`someToast = L("中文")`、`return L("中文")`
  - 插值:`Text(Lf("已 %@/%@ 人", "\(a)", "\(b)"))` —— key 用 `%@` 占位、数字转字符串传入(避免格式宽度坑;`%` 折进字符串参数避免 `%%`)。

## 加新串的流程
1. 写代码:能用双语 `"中文 / English"` 就用(自动拆);否则 `Text(L("中文"))` 或 `Lf(...)`。
2. 纯中文 key 若需要英文:在 `translations.json` 加 `"中文": "English"`(插值 key 形如 `"已 %@ 件": "%@ items"`)。
3. `python tools/i18n/gen_strings.py`
4. 提交改动的 `.swift` + 重新生成的 `en.lproj/zh-Hans.lproj` + `translations.json`。

## 后端联动
`/chat/stream` 接收可选 `language`(`zh`/`en`,缺省 `zh`)→ 导购助手按该语言回复。iOS 端在请求里带上当前语言即可,向后兼容(老客户端不传 = 中文)。

## 文件
- `gen_strings.py` — 生成器(自包含,stdlib,仓库相对路径)。
- `translations.json` — 纯中文串的中→英映射(唯一需要人工维护的翻译源)。
