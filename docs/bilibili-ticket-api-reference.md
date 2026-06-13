# Bilibili 会员购实名非选座电子票 API 接口文档

版本：`v0.5-realname-nonseat-eticket`  
日期：2026-05-22  
用途：给“实名 + 非选座 + 电子票”下单程序实现提供接口目录、请求头基线、请求参数、关键字段和证据等级。  

当前交付范围不覆盖配送纸质票。若确认页进入地址分支或需要 `deliver_info`，实现应返回 out-of-scope，而不是猜测配送 body。

## 1. 证据等级

| 等级 | 含义 |
| --- | --- |
| `Captured` | 当前抓到真实 request 或 response |
| `Code-Verified` | 官方前端包确认 method、URL 或 body 构造 |
| `Observed` | 页面资源列表观察到 URL |
| `Pending-HAR` | 仍需 authenticated HAR 固化 body/schema |

## 2. 请求头基线

### 2.1 详情接口抓到的 headers

```http
Content-Type: application/json
Referer: https://mall.bilibili.com/neul-next/ticket-renovation/detail.html?id={project_id}&noTitleBar=1
User-Agent: {current UA}
```

### 2.2 票务 `show.bilibili.com` 基线

下单相关请求至少保留：

```http
Cookie: {logged-in browser session cookies}
Referer: {current ticket page}
Origin: https://mall.bilibili.com
User-Agent: {same browser session UA}
Content-Type: application/json | application/x-www-form-urlencoded as emitted by page client
```

当前真实创建单抓到的额外 header：

```http
x-risk-header: platform/h5 uid/{uid} channel/1 deviceId/{risk-device-id}
```

运行时还可能出现：

- `x-risk-header` 或等价风险上下文 header。
- 设备与来源 query/body 字段。
- `oaccesskey` query 追加。

程序要求：

- 同一订单链路使用同一浏览器 session 与 UA。
- 不跨会话复用 prepare token、payment token、voucher。
- API 文档中的 Cookie 只写占位符，不保存账号原值。

## 3. 详情

### 3.1 详情主数据

| 项 | 值 |
| --- | --- |
| Evidence | `Captured` |
| Method | `POST` |
| URL | `https://mall.bilibili.com/mall-search-items/items_detail/info` |
| Auth | 公共详情可请求，登录态影响用户字段 |
| Content-Type | `application/json` |

Body：

```json
{
  "itemsId": 1001300,
  "itemsDetailPageType": 3
}
```

关键参数：

| 字段 | 必要性 | 说明 |
| --- | --- | --- |
| `itemsId` | required | 项目 ID |
| `itemsDetailPageType` | required in captured renovation detail | 当前抓到值 `3` |

响应关键路径：

| Path | 用途 |
| --- | --- |
| `success` | 是否成功 |
| `data.floorKeys` | 详情楼层 |
| `data.basicInfoFloorVO` | 基础项目数据 |
| footer/sale 相关楼层 | 场次、票档、售卖状态，字段需建模 |

### 3.2 详情辅助接口

| Evidence | Method | URL | 作用 |
| --- | --- | --- | --- |
| `Observed` | GET | `https://show.bilibili.com/ticket-c/user/getUserLoginInfo` | 登录态 |
| `Observed` | GET | `https://show.bilibili.com/api/ticket/order/exhibit` | 详情订单辅助 |
| `Observed` | GET | `https://show.bilibili.com/api/ticket/promo/exhibit` | 促销展示 |
| `Observed` | GET | `https://show.bilibili.com/api/ticket/linkgoods/list` | 关联商品 |

### 3.3 旧详情与热门样例

| 项 | 值 |
| --- | --- |
| Evidence | `Captured` |
| Method | `GET` |
| URL | `https://show.bilibili.com/api/ticket/project/getV2` |

当前热门实名非选座样例请求：

```text
GET /api/ticket/project/getV2
  ?id=102626
  &project_id=102626
  &requestSource=neul-next
```

当前响应中已确认：

| Path | Captured value | 用途 |
| --- | --- | --- |
| `data.hotProject` | `true` | 热门项目标志 |
| `data.id_bind` | `2` | 实名要求 |
| `data.screen_list[].jump_page` | `"confirm"` | 非选座确认页链路 |
| `data.buyer_info` | response field present | prepare/确认页 buyer 规则来源 |

新增排查样例：

| Project | `id_bind` | `hotProject` | `has_eticket` | `has_paper_ticket` | `screen_list[].jump_page` | `screen_list[].delivery_type` |
| --- | --- | --- | --- | --- | --- | --- |
| `102194` | `2` | `true` | `true` | `false` | `"confirm"` | `1` |
| `100596` | `2` | `false` | `true` | `false` | `"confirm"` | `1` |

这两个已结束项目都落在实名非选座电子票范围，不触发配送地址分支。

## 4. Prepare

### 4.1 通用 prepare

| 项 | 值 |
| --- | --- |
| Evidence | `Captured` for实名非选座样例, `Code-Verified` for wrapper |
| Method | `POST` |
| URL | `https://show.bilibili.com/api/ticket/order/prepare?project_id={project_id}` |

前端包装追加：

```json
{
  "newRisk": true,
  "requestSource": "neul-next"
}
```

通用字段：

| 字段 | 分支 | 关键性 |
| --- | --- | --- |
| `project_id` | all | required |
| `screen_id` | all | required |
| `order_type` | all | required |
| `count` | all | required |
| `token` | hot/entry context | conditional |
| `voucher` | verified request | conditional |
| `newRisk` | page wrapper | required by current frontend |
| `requestSource` | page wrapper | required by current frontend |

成功响应字段：

| Path | 用途 |
| --- | --- |
| `errno` | `0` 成功 |
| `data.token` | 确认页 token |
| `data.ptoken` | 部分分支由 `prepare` 下发，传确认页与创建链路 |
| `data.shield` | 风控验证 |

当前实名非选座样例成功响应已直接补抓：

```json
{
  "errno": 0,
  "msg": "",
  "data": {
    "token": "{prepare-token}",
    "shield": {
      "open": 0
    },
    "project_name": null,
    "screen_name": null,
    "project_img": null,
    "ga_data": {
      "risk_level": 1,
      "grisk_id": "{prepare-risk-id}",
      "decisions": [],
      "riskParams": null,
      "riskResult": 0,
      "open": null
    },
    "success_seats": null,
    "failed_seats": null,
    "ptoken": null
  },
  "errtag": 0
}
```

### 4.2 实名非选座票

`orderObj` 初始字段：

```json
{
  "project_id": "...",
  "screen_id": "...",
  "sku_id": "...",
  "count": "...",
  "pay_money": "...",
  "order_type": 1,
  "timestamp": null,
  "deliver_type": "...",
  "buyer_info": "..."
}
```

当前实名非选座样例真实 body：

```json
{
  "project_id": 1001300,
  "screen_id": 1004266,
  "order_type": 1,
  "count": 1,
  "sku_id": 875706,
  "buyer_info": "2,1",
  "token": "",
  "ignoreRequestLimit": true,
  "ticket_agent": "",
  "newRisk": true,
  "requestSource": "neul-next"
}
```

关键标记：

| 字段 | 标记 |
| --- | --- |
| `sku_id` | selected SKU |
| `pay_money` | 前端当次价格 |
| `deliver_type` | 交付边界；当前目标只接受电子票 |
| `buyer_info` | 详情/确认规则上下文 |
| `ignoreRequestLimit` | 当前样例 page-emitted field |
| `ticket_agent` | 页面状态，当前样例为空串 |

## 5. 确认订单

### 5.1 confirmInfo

| 项 | 值 |
| --- | --- |
| Evidence | `Captured`, `Code-Verified` |
| Method | `GET` |
| URL | `https://show.bilibili.com/api/ticket/order/confirmInfo` |

Query：

| 字段 | 必要性 | 说明 |
| --- | --- | --- |
| `token` | required | prepare token |
| `timestamp` | required in observed request | 当前时间 |
| `project_id` | required | 项目 ID |
| `screen_id` | conditional | 当前实名非选座样例未带，保留页面实际 query |
| `requestSource=neul-next` | frontend wrapper | 当前强制追加 |
| `ptoken` | risk branch | `prepare.data.ptoken` 被详情页追加进确认页 URL 后透传 |
| `oaccesskey` | entry branch | 入口携带 |

响应关键字段：

| Path | 用途 |
| --- | --- |
| `count` | 数量 |
| `ticket_info` | 票档与 SKU |
| `buyerList.list[]` | 账号中可用购票人 |
| `buyer_info` | buyer 规则字符串 |
| `id_bind` | 实名规则 |
| `need_contact` | 联系人规则 |
| `contact_info` | 联系人缓存 |
| `deliver_type` | 交付类型 |
| `express_type` / `express_fee` | 响应中的交付字段；当前电子票主链不使用 |
| `item_total_money` / `pay_money` | 金额 |
| `hotProject` | 热门项目 |
| `orderCreateUrl` | 创建 URL 覆盖 |
| `purchase_agreement` | 条款勾选要求 |

当前实名非选座样例响应补充：

| Path | Captured value |
| --- | --- |
| `data.buyer_info` | `"2,1"` |
| `data.id_bind` | `2` |
| `data.need_contact` | `0` |
| `data.hotProject` | `false` |
| `data.ticket_info.sku_id` | `875706` |

## 6. Buyer 与确认页辅助

### 6.1 buyer list

| Evidence | Method | URL | 参数 |
| --- | --- | --- | --- |
| `Code-Verified` | GET | `https://show.bilibili.com/api/ticket/buyer/list` | `channel=3`, `nomask=1` |

### 6.2 buyer mutate

| Evidence | Method | URL |
| --- | --- | --- |
| `Code-Verified` | POST | `https://show.bilibili.com/api/ticket/buyer/create` |
| `Code-Verified` | POST | `https://show.bilibili.com/api/ticket/buyer/update` |
| `Code-Verified` | POST | `https://show.bilibili.com/api/ticket/buyer/delete` |

字段已见：

- `id`
- `name`
- `tel`
- `personal_id`
- `personal_id_type`
- `personal_id_type_name`
- `is_default`

### 6.3 stock check

| 项 | 值 |
| --- | --- |
| Evidence | `Captured`, `Code-Verified` |
| Method | `POST` |
| URL | `https://show.bilibili.com/api/ticket/stock/check` |

Body：

```json
{
  "projectId": "...",
  "skuId": "...",
  "screenId": "..."
}
```

### 6.4 contact

| Evidence | Method | URL | 参数 |
| --- | --- | --- | --- |
| `Code-Verified` | POST | `https://show.bilibili.com/api/ticket/buyer/saveContactInfo` | `username`, `tel` |

## 7. 创建订单

### 7.1 createV2

| 项 | 值 |
| --- | --- |
| Evidence | `Captured` for实名非选座样例, `Code-Verified` for branch fields |
| Method | `POST` |
| URL | `https://show.bilibili.com/api/ticket/order/createV2?project_id={project_id}` |

URL 规则：

- `orderCreateUrl` 可覆盖默认 URL。
- `ptoken` 存在时追加 `&ptoken={ptoken}`。
- `oaccesskey` 存在时包装层追加。

公共 body 字段：

| 字段 | 标记 |
| --- | --- |
| `token` | prepare token |
| `timestamp` | current timestamp |
| `project_id` | project |
| `screen_id` | screen |
| `sku_id` | SKU |
| `count` | quantity |
| `pay_money` | payable amount |
| `order_type` | order kind |
| `id_bind` | buyer rule |
| `need_contact` | contact rule |
| `requestSource` | `neul-next` |
| `newRisk` | `true` |

分支 body 字段：

| 字段 | 条件 | 说明 |
| --- | --- | --- |
| `buyer_info` |实名 | JSON 字符串 |
| `buyer` | contact | 联系人姓名 |
| `tel` | contact | 联系人手机号 |
| `coupon_code` | coupon | 优惠券 |
| `freeCode` | free pass | 免费票分支 |
| `use_year_card` | year card | 年卡 |
| `link_id` | link goods | 联动 |
| `voucher` | verified request | 当次验证凭证 |
| `ticket_agent` | browser state | 票务 agent |
| `clickPosition` | click event exists | 点击上下文 |
| `ctoken` | hotProject | `Collect.encode()` 生成的行为采集编码值 |
| `deviceId` | browser state | 设备 ID |
| `deviceFingerprint` | branch | 设备指纹 |

当前实名非选座样例真实 body，个人信息已脱敏：

```json
{
  "project_id": 1001300,
  "screen_id": 1004266,
  "count": 1,
  "pay_money": 29800,
  "order_type": 1,
  "timestamp": 1779443348507,
  "id_bind": 2,
  "need_contact": 0,
  "contactNoticeText": "",
  "is_package": 0,
  "package_num": 1,
  "contactInfo": null,
  "sku_id": 875706,
  "coupon_code": "",
  "again": 0,
  "token": "{prepare-token}",
  "deviceId": "{device-id}",
  "buyer_info": "[{\"id\":\"{buyer-id}\",\"name\":\"{masked}\",\"tel\":\"{masked}\",\"personal_id\":\"{masked}\",\"id_type\":0}]",
  "clickPosition": {
    "x": 498,
    "y": 1213,
    "origin": 1779443285743,
    "now": 1779443348507
  },
  "requestSource": "neul-next",
  "newRisk": true
}
```

成功响应：

```json
{
  "errno": 0,
  "msg": "",
  "data": {
    "orderId": "{order-id}",
    "orderCreateTime": "{unix-seconds}",
    "token": "{create-status-token}"
  },
  "errtag": 0
}
```

## 8. 创建状态与支付

### 8.1 createstatus

| 项 | 值 |
| --- | --- |
| Evidence | `Captured`, `Code-Verified` |
| Method | `GET` |
| URL | `https://show.bilibili.com/api/ticket/order/createstatus` |

关键 query：

| 字段 | 说明 |
| --- | --- |
| `token` | `createV2` 返回创建 token |
| `project_id` | 当前项目 |
| `orderId` | `createV2.data.orderId`，当前真实请求已带 |

成功响应关键路径：

| Path | 用途 |
| --- | --- |
| `data.order_id` | 支付前订单 ID |
| `data.payParam` | 收银台参数 |
| `data.payParam.orderId` | 支付订单 ID |
| `data.payParam.payAmount` | 支付金额 |
| `data.payParam.originalAmount` | 原始金额 |
| `data.payParam.orderExpire` | 订单支付有效期 |
| `data.payParam.productId` | SKU |
| `data.payParam.returnUrl` | 支付回跳 |
| `data.payParam.sign` | 服务端支付签名 |
| `data.payParam.code_url` | 二维码支付 URL |
| `data.payParam.expire_time` | 二维码有效期 |

当前样例响应结构：

```json
{
  "errno": 0,
  "msg": "",
  "data": {
    "payParam": {
      "customerId": 10001,
      "defaultChoose": "wechat",
      "deviceType": 1,
      "feeType": "CNY",
      "orderExpire": "600",
      "orderId": "{order-id}",
      "originalAmount": 29800,
      "payAmount": 29800,
      "productId": 875706,
      "returnUrl": "{return-url}",
      "signType": "MD5",
      "timestamp": "{server-timestamp}",
      "traceId": "{trace-id}",
      "version": "1.0",
      "sign": "{server-sign}",
      "code_url": "{cashier-qr-url}",
      "expire_time": 300,
      "pay_type": 1
    },
    "order_id": "{order-id}"
  },
  "errtag": 0
}
```

### 8.2 支付辅助

| Evidence | Method | URL |
| --- | --- | --- |
| `Code-Verified` | GET | `https://show.bilibili.com/api/ticket/order/getPayParam` |
| `Code-Verified` | GET | `https://show.bilibili.com/api/ticket/order/payChannelList` |
| `Code-Verified` | POST | `https://show.bilibili.com/api/ticket/order/display` |
| `Code-Verified` | GET | `https://show.bilibili.com/api/ticket/order/cancel` |

### 8.3 支付收银台

| 项 | 值 |
| --- | --- |
| Evidence | `Captured` |
| URL | `https://pay.bilibili.com/payplatform-h5/cashierdesk.html` |

已见 pay params：

- `orderId`
- `orderCreateTime`
- `payAmount`
- `originalAmount`
- `orderExpire`
- `customerId`
- `productId`
- `traceId`
- `returnUrl`
- `code_url`
- `signType`
- `sign`

## 9. 风控与验证码

### 9.1 graph

| Evidence | Method | URL |
| --- | --- | --- |
| `Code-Verified` | GET | `https://show.bilibili.com/api/ticket/graph/prepare` |
| `Code-Verified` | POST | `https://show.bilibili.com/api/ticket/graph/check` |

已见参数：

| 接口 | 字段 |
| --- | --- |
| `graph/prepare` | `project_id`, `screen_id`, `timestamp` |
| `graph/check` | `project_id`, `screen_id`, `voucher`, `challenge`, `validate`, `seccode`, `success` |

### 9.2 风险字段关键标记

| 字段 | 请求位置 | 构造路径 |
| --- | --- | --- |
| `prepare.token` | prepare body | hotProject 时 `Collect.encode()` |
| `ctoken` | createV2 body | hotProject 时 `Collect.encode()` |
| `ptoken` | confirmInfo/createV2 URL query | `prepare.data.ptoken` 经确认页 URL 透传 |
| `voucher` | prepare/create body | 当次验证结果 |

### 9.3 `Collect.encode()` 字段与布局

官方 bundle 模块 `66281` 已确认 `Collect` 实现。

采集来源：

| 采集项 | 触发或来源 |
| --- | --- |
| touch end count | `document.touchend` |
| visible count | `visibilitychange` 进入 `visible` |
| open window count | 包装后的 `exec("openWindow")` |
| interval seconds | `setInterval` 每秒自增 |
| elapsed from prepare | `Date.now() - localStorage.ticket_collection_t` |
| scroll/window/screen metrics | 初始化时的 `scrollX`, `scrollY`, inner/outer size, screen position/size |

16 字节布局：

| Offset | Field | Type |
| --- | --- | --- |
| `0` | touch end count | uint8 |
| `1` | scrollX | uint8 |
| `2` | visible count | uint8 |
| `3` | scrollY | uint8 |
| `4` | innerWidth | uint8 |
| `5` | open window count | uint8 |
| `6` | innerHeight | uint8 |
| `7` | outerWidth | uint8 |
| `8..9` | interval seconds | uint16 |
| `10..11` | elapsed from prepare | uint16 |
| `12` | outerHeight | uint8 |
| `13` | screenX | uint8 |
| `14` | screenY | uint8 |
| `15` | screen.width | uint8 |

编码过程：

1. 写入 16 字节 `ArrayBuffer`。
2. `uint8` 字段上限 `255`，`uint16` 字段上限 `65535`。
3. 字节序列经 `Uint16Array` 转换后 `btoa`。
4. 输出值用于热门 `prepare.token` 或 `createV2.ctoken`。

`ptoken` 当前不在前端 bundle 中本地计算。已确认透传链：

```text
prepare response data.ptoken
  -> confirmOrder URL query ptoken
  -> confirmInfo query ptoken
  -> createV2 URL query ptoken
```

当前工作区复刻实现：

- `bilibili-ctoken-generator.js`

可直接使用两种入口：

| 入口 | 用途 |
| --- | --- |
| `new CollectTokenGenerator(...).encode()` | 复刻页面采集与 `localStorage` 连续状态 |
| `encodeCollectToken(state)` | 对已准备好的采集快照做固定 16 字节编码 |

热门详情页在生成 `prepare.body.token` 前应先记录 `ticket_collection_t`；热门确认页应继续读取 `ticket_collection`，再生成 `createV2.body.ctoken`。

字段使用关系：

| 阶段 | Field | Source |
| --- | --- | --- |
| hot prepare | `body.token` | `Collect.encode()` |
| hot createV2 | `body.ctoken` | `Collect.encode()` |

这两个字段来自同一编码器，但不是同一次编码，行为计数与 `prepareElapsedSeconds` 会导致值不同。

## 10. 待补 HAR 表

| 接口 | 当前状态 |
| --- | --- |
| 非选座 `order/prepare` response | 成功 body 已直接补抓 |
| hotProject `prepare/createV2` | `data.hotProject=true` 与字段发送路径已确认，缺可售热门实名非选座下单请求样例 |
