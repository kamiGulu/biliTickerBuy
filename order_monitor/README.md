# B站未支付订单监控

这是一个和主项目分离的独立小程序，只做一件事：

- 每 60 秒轮询一次 `ticketList`
- 发现新的未支付订单时发送 QQ 邮件
- 同时播放本地音乐提醒

## 目录说明

- `monitor.py`: 监控主程序
- `config.example.json`: 配置示例
- `requirements.txt`: 独立依赖

## 使用方式

1. 准备 Python 3.11+
2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 复制配置

```bash
cp config.example.json config.json
```

4. 修改 `config.json`

- `cookies_file`: 可以直接指向主项目现有的 `cookies.json`
- `cookie`: 如果你想直接粘贴浏览器整段 Cookie，也可以填这里；填了以后会优先使用它
- `audio_file`: 本地告警音频文件，支持 mp3/wav
- `mail`: QQ 邮箱 SMTP 配置

5. 启动监控

```bash
python monitor.py --config config.json
```

只跑一轮自检：

```bash
python monitor.py --config config.json --once
```

## 直接测试

### 1. 测试邮件发送

```bash
python monitor.py --config config.json --test-email
```

### 2. 测试音频播放

```bash
python monitor.py --config config.json --test-audio
```

说明：

- 这会直接播放 `audio_file` 指向的音频
- 需要停止时，按 `Ctrl+C`
- 脚本退出时会主动停止音乐，不会让音频继续挂着播
- Windows 默认不依赖额外的 Python 音频库

### 3. 同时测试邮件和音频

```bash
python monitor.py --config config.json --test-notify
```

执行顺序是：

- 先发测试邮件
- 再播放测试音频
- 播放过程中按 `Ctrl+C` 可以停止音乐并退出

## Linux 部署建议

### 1. 播放音频

如果你的 Linux 机器没有声音或音频依赖不完整，建议直接安装一个命令行播放器：

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

脚本会优先自动寻找 `ffplay`、`mpv`、`mpg123`、`paplay`、`aplay`。

如果你想手动指定播放器，也可以在 `config.json` 里加：

```json
"audio_player_command": ["ffplay", "-nodisp", "-autoexit", "{audio_file}"]
```

### 2. 后台运行

```bash
nohup python monitor.py --config config.json > monitor.out 2>&1 &
```

## 触发规则

- 只对“新的未支付订单”报警一次，避免每分钟重复发邮件
- 如果订单消失了，状态文件会把它从已提醒集合里移除
- 下次再出现新的未支付订单，会再次提醒

## 邮件内容

邮件里会带上：

- 订单号
- 项目名称
- 场次
- 票档
- 当前状态
- 剩余支付时间

## 注意事项

- `SESSDATA`、`bili_jct` 这些 Cookie 过期后，监控会请求失败，需要重新更新 Cookie
- QQ 邮箱要提前开启 SMTP，并使用授权码，不要直接用邮箱登录密码
- 如果你部署在纯命令行 Linux 服务器上，没有声音输出设备，邮件提醒仍然会工作
