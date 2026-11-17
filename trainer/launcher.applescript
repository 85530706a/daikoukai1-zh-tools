set instDir to (POSIX path of (path to application support folder from user domain)) & "DHHTrainer"
set startupScript to instDir & "/start-trainer.sh"
set py to "/opt/homebrew/bin/python3"
try
	do shell script "test -x " & quoted form of py
on error
	set py to "/usr/bin/python3"
end try

try
	do shell script "/bin/sh " & quoted form of startupScript & " " & quoted form of instDir & " " & quoted form of py with prompt "大航海时代实时修改器需要读写 DOSBox-X 的内存。" with administrator privileges
on error errorText number errorNumber
	if errorNumber is not -128 then
		do shell script "/usr/bin/printf '%s\\n' " & quoted form of errorText & " > " & quoted form of (instDir & "/launcher-error.log")
		display dialog "修改器启动失败。" & return & return & errorText buttons {"好"} default button 1 with icon caution
	end if
	return
end try
open location "http://127.0.0.1:8765/"

