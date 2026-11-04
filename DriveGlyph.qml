import QtQuick
import qs.Commons
import qs.Commons as Commons
Item {
  property string name: "arrow-up"
  property color color: Commons.Color.muted
  implicitWidth: 16
  implicitHeight: 16
  Canvas {
    anchors.fill: parent
    property string icon: parent.name
    property color ink: parent.color
    onIconChanged: requestPaint()
    onInkChanged: requestPaint()
    onPaint: {
      var c = getContext("2d"); c.reset(); c.scale(width/24,height/24);
      c.strokeStyle=ink; c.lineWidth=1.6; c.lineCap="round"; c.lineJoin="round";
      function line(points) { c.beginPath(); c.moveTo(points[0],points[1]); for(var i=2;i<points.length;i+=2)c.lineTo(points[i],points[i+1]); c.stroke(); }
      if(icon.indexOf("circle")===0){c.beginPath();c.arc(12,12,9,0,Math.PI*2);c.stroke();}
      if(icon==="circle-alert") {line([12,8,12,12]);line([12,16,12,16.1]);}
      else if(icon==="folder-open") {line([3,18,3,6,9,6,11,8,20,8,20,10]);line([3,18,7,11,22,11,18,19,3,19]);}
      else if(icon==="rotate-cw") {c.beginPath();c.arc(12,12,8,-Math.PI/2,Math.PI*1.2);c.stroke();line([20,4,20,10,14,10]);}
      else if(icon.indexOf("down")>=0) {line([12,6,12,18]);line([7,13,12,18,17,13]);}
      else {line([12,18,12,6]);line([7,11,12,6,17,11]);}
    }
  }
}
