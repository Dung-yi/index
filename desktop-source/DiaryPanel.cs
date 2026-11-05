using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class DiaryPanel : Form {
    readonly string root, python;
    readonly bool preview;
    Label status;
    List<Button> buttons = new List<Button>();
    Process process;
    string resultPath, action, package;
    Timer timer = new Timer();
    JavaScriptSerializer json = new JavaScriptSerializer();
    static string Quote(string s) { return "\""+s+"\""; }
    public DiaryPanel(string appRoot, string pythonPath, bool isPreview) {
        root=appRoot; python=pythonPath; preview=isPreview;
        Text="每日工作日誌｜啟動中心"; ClientSize=new Size(530,450);
        StartPosition=FormStartPosition.CenterScreen; FormBorderStyle=FormBorderStyle.FixedDialog;
        MaximizeBox=false; BackColor=Color.FromArgb(241,245,249);
        Font=new Font("Microsoft JhengHei UI",10);
        Icon=new Icon(Path.Combine(root,"diary.ico"));
        Label heading=new Label {Text="每日工作日誌",Bounds=new Rectangle(0,0,530,94),TextAlign=ContentAlignment.MiddleCenter,
            BackColor=Color.FromArgb(30,41,59),ForeColor=Color.White,Font=new Font("Microsoft JhengHei UI",23,FontStyle.Bold)};
        Controls.Add(heading);
        var manifest=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"manifest.json"),Encoding.UTF8));
        Controls.Add(new Label {Text="本機主機版  v"+manifest["version"],Bounds=new Rectangle(20,108,490,26),TextAlign=ContentAlignment.MiddleCenter});
        status=new Label {Text="正在準備開啟工作日誌…",Bounds=new Rectangle(22,140,486,48),TextAlign=ContentAlignment.MiddleCenter};
        Controls.Add(status);
        Add("開啟工作日誌",28,202,230,delegate {Run("open",null);},true);
        Add("檢查更新",272,202,230,delegate {Run("check",null);},false);
        Add("從更新檔安裝",28,264,230,delegate {
            using(var dialog=new OpenFileDialog {Title="選擇工作日誌更新檔",Filter="工作日誌更新 ZIP (*.zip)|*.zip"}) {
                if(dialog.ShowDialog(this)==DialogResult.OK) Run("inspect-file",dialog.FileName);
            }
        },false);
        Add("立即備份資料",272,264,230,delegate {Run("backup",null);},false);
        Add("停止主機",28,326,474,delegate {
            if(MessageBox.Show(this,"停止後，電腦和手機都無法開啟工作日誌。確定停止？","停止主機",MessageBoxButtons.YesNo,MessageBoxIcon.Question)==DialogResult.Yes) Run("stop",null);
        },false);
        Controls.Add(new Label {Text="關閉啟動中心，主機會繼續運行。\n要結束服務，請按「停止主機」。",
            Bounds=new Rectangle(20,390,490,46),TextAlign=ContentAlignment.MiddleCenter,ForeColor=Color.SlateGray,Font=new Font("Microsoft JhengHei UI",9)});
        timer.Interval=200; timer.Tick+=Poll; timer.Start();
        Shown+=delegate {Run("open",null);};
        FormClosing+=delegate(object sender,FormClosingEventArgs e) {
            if(process!=null) {e.Cancel=true; MessageBox.Show(this,"正在處理，請完成後再關閉。","工作日誌");}
        };
    }
    void Add(string text,int x,int y,int width,Action click,bool primary) {
        Button b=new Button {Text=text,Bounds=new Rectangle(x,y,width,49),FlatStyle=FlatStyle.Flat,
            BackColor=primary?Color.FromArgb(37,99,235):Color.White,ForeColor=primary?Color.White:Color.FromArgb(30,41,59)};
        b.FlatAppearance.BorderSize=0; b.Click+=delegate {click();}; buttons.Add(b); Controls.Add(b);
    }
    void Run(string command,string file) {
        if(process!=null) return;
        action=command; package=file;
        if(preview) {status.Text="主機運行中，可在瀏覽器使用工作日誌。（測試畫面）"; return;}
        status.Text="正在處理，請稍候…";
        foreach(Button b in buttons) b.Enabled=false;
        try {
            Directory.CreateDirectory(Path.Combine(root,"data"));
            resultPath=Path.Combine(root,"data","action-"+Guid.NewGuid().ToString("N")+".json");
            string args=Quote(Path.Combine(root,"launcher.py"))+" --action "+command+" --result "+Quote(resultPath);
            if(file!=null) args+=" --package "+Quote(file);
            process=Process.Start(new ProcessStartInfo(python,args) {UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=root});
        } catch(Exception) {FinishButtons(); status.Text="無法啟動，請確認 Python 安裝及資料夾權限。";}
    }
    void FinishButtons() {if(process!=null) process.Dispose(); process=null; foreach(Button b in buttons)b.Enabled=true;}
    void Poll(object sender,EventArgs e) {
        if(process==null || !process.HasExited) return;
        FinishButtons();
        try {
            var reply=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(resultPath,Encoding.UTF8));
            File.Delete(resultPath);
            string message=Convert.ToString(reply["message"]); status.Text=message;
            if(!Convert.ToBoolean(reply["ok"])) {MessageBox.Show(this,message,"操作未完成",MessageBoxButtons.OK,MessageBoxIcon.Warning); return;}
            if(action=="check" || action=="inspect-file") {
                if(Convert.ToBoolean(reply["newer"])) {
                    string question="版本 "+reply["version"]+"\n"+reply["notes"]+"\n\n安裝時會先備份並暫停主機。\n請先儲存網頁中的修改。現在更新？";
                    if(MessageBox.Show(this,question,"發現新版",MessageBoxButtons.YesNo,MessageBoxIcon.Question)==DialogResult.Yes)
                        Run(action=="check"?"online-install":"file-install",package);
                } else MessageBox.Show(this,message,"檢查更新",MessageBoxButtons.OK,MessageBoxIcon.Information);
            } else if(action=="backup") MessageBox.Show(this,message,"備份完成",MessageBoxButtons.OK,MessageBoxIcon.Information);
            else if(reply.ContainsKey("version")) MessageBox.Show(this,"更新完成，請關閉此啟動中心，再按桌面捷徑開啟新版。\n網頁已重新開啟。","更新完成",MessageBoxButtons.OK,MessageBoxIcon.Information);
        } catch(Exception) {status.Text="未能取得操作結果。請保留資料並檢查 data/panel.log。";}
    }
    static void Set(object obj,string key,object value) {obj.GetType().InvokeMember(key,BindingFlags.SetProperty,null,obj,new object[]{value});}
    static object Get(object obj,string key) {return obj.GetType().InvokeMember(key,BindingFlags.GetProperty,null,obj,null);}
    static void Shortcut(string root,string python) {
        string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"每日工作日誌.lnk");
        object shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        object link=shell.GetType().InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{path});
        string args=Quote(Path.Combine(root,"launcher.py"));
        if(File.Exists(path) && Convert.ToString(Get(link,"Arguments"))!=args) throw new Exception("桌面已有另一個同名捷徑，未覆蓋。請先重新命名後再試。");
        Set(link,"TargetPath",python); Set(link,"Arguments",args); Set(link,"WorkingDirectory",root);
        Set(link,"IconLocation",Path.Combine(root,"diary.ico")+",0"); Set(link,"Description","啟動每日工作日誌，提供更新、備份與主機管理");
        link.GetType().InvokeMember("Save",BindingFlags.InvokeMethod,null,link,null);
    }
    [STAThread] static int Main(string[] args) {
        string root=null,python=null; bool preview=false,shortcut=false,quiet=false;
        for(int i=0;i<args.Length;i++) {
            if(args[i]=="--root") root=args[++i]; else if(args[i]=="--python") python=args[++i];
            else if(args[i]=="--preview") preview=true; else if(args[i]=="--shortcut") shortcut=true; else if(args[i]=="--quiet") quiet=true;
        }
        try {
            if(root==null) root=AppDomain.CurrentDomain.BaseDirectory;
            if(python==null) python=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\pythonw.exe");
            if(!File.Exists(python)) throw new Exception("請使用桌面捷徑或啟動.cmd 開啟。");
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            if(shortcut) {Shortcut(root,python); if(!quiet)MessageBox.Show("桌面捷徑已建立。","每日工作日誌");}
            else Application.Run(new DiaryPanel(root,python,preview));
            return 0;
        } catch(Exception error) {if(!quiet)MessageBox.Show(error.Message,"每日工作日誌",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1;}
    }
}
